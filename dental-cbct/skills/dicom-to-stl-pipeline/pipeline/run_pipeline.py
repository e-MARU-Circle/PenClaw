#!/usr/bin/env python3
"""DICOM → NIfTI → nnU-Netv2 → STL を 1 コマンドで実行するヘッドレス・パイプライン。

DICOM_to_STL アプリ (v4.4 / main_app.py) のロジックを GUI・PyInstaller 依存なしに
再構成したもの。学習モデル本体と venv は同梱しない（各自が用意する。再配布禁止）。

設計上の原則（プライバシー）:
  - 本スクリプトは DICOM の画素・タグを **標準出力に一切表示しない**。
    進捗・ファイル名・件数のみをログする。呼び出し側エージェントは
    DICOM/NIfTI の中身を読まず、パスを渡して終了コードだけを見ればよい。
  - PHI 除去は anonymize_dicom.py（別ステップ）で行う。本スクリプトは
    --require-anonymized 指定時、入力に PHI が残っていれば実行を拒否する。

使い方の例:
  python3 run_pipeline.py \
      --in  /path/to/ANON_DICOM_DIR \
      --out /path/to/STL_OUTPUT_DIR \
      --model-dir /path/to/nnUNet_results \
      --device auto --accept-disclaimer

終了コード: 0=成功 / 2=パイプラインエラー / 3=PHIガード違反 / 99=想定外
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Iterable, Optional

# 既定は公開モデル DentalSegmentator（Zenodo 10829675, CC-BY-4.0）。
# 解凍後のフォルダ名をそのまま使う（例: Dataset112_DentalSegmentator）。
# 自前モデルを使う場合は --dataset で上書き（例: Dataset111_453CT）。
DEFAULT_DATASET = "Dataset112_DentalSegmentator"
DEFAULT_CONFIGURATION = "3d_fullres"
DEFAULT_FOLD = "0"
TARGET_LABELS = [1, 2, 3, 4, 5]

DISCLAIMER = (
    "本ソフトウェアは薬機法上の医療機器ではなく、研究用途に限定されます。"
    "診断・治療の根拠として使用しないでください。出力結果について一切の保証・"
    "責任を負いません。同意する場合は --accept-disclaimer を付けて実行してください。"
)

Log = Callable[[str], None]


class PipelineError(Exception):
    """パイプラインのいずれかの工程が失敗したときに送出。"""


class PHIGuardError(Exception):
    """匿名化されていない入力を検出したときに送出。"""


def _log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# 外部実行ファイルの解決
# --------------------------------------------------------------------------- #
def resolve_dcm2niix(explicit: Optional[str]) -> str:
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit
        raise PipelineError(f"指定された dcm2niix が実行できません: {explicit}")
    found = shutil.which("dcm2niix") or shutil.which("dcm2niix.exe")
    if found:
        return found
    # スキル同梱バイナリ（pipeline/bin/）を最後に探す
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("dcm2niix", "dcm2niix.exe"):
        cand = os.path.join(here, "bin", name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    raise PipelineError(
        "dcm2niix が見つかりません。PATH に追加するか --dcm2niix で指定してください。"
    )


# --------------------------------------------------------------------------- #
# デバイス検出
# --------------------------------------------------------------------------- #
def detect_device(preferred: str, log: Log) -> str:
    available = ["cpu"]
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            available.insert(0, "cuda")
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            idx = 1 if available and available[0] == "cuda" else 0
            available.insert(idx, "mps")
    except Exception as exc:  # noqa: BLE001
        log(f"PyTorch 検出をスキップ（CPU 前提）: {exc}")
    available = list(dict.fromkeys(available))

    preferred = preferred.lower()
    if preferred == "auto":
        chosen = available[0]
        log(f"利用可能デバイス: {', '.join(available)} → '{chosen}' を使用")
        return chosen
    if preferred not in available:
        log(f"デバイス '{preferred}' は不可。利用可能: {', '.join(available)}。CPU へフォールバック")
        return "cpu"
    log(f"デバイス '{preferred}' を使用")
    return preferred


# --------------------------------------------------------------------------- #
# PHI ガード（軽量チェック。値は表示しない）
# --------------------------------------------------------------------------- #
def assert_anonymized(dicom_dir: str, log: Log) -> None:
    """匿名化されていなければ PHIGuardError。タグ値は出力しない。

    判定:
      - anonymize_dicom.py が立てる PatientIdentityRemoved == "YES" があれば合格。
      - マーカーが無い場合は、生の識別子タグ（氏名/ID/生年月日/住所/電話）が
        非空なら未匿名化とみなして拒否する。
    """
    try:
        import pydicom  # type: ignore
    except Exception:
        log("注意: pydicom 未導入のため PHI ガードを省略します（匿名化済み前提で続行）。")
        return

    phi_tags = [
        (0x0010, 0x0010),  # PatientName
        (0x0010, 0x0020),  # PatientID
        (0x0010, 0x0030),  # PatientBirthDate
        (0x0010, 0x1040),  # PatientAddress
        (0x0010, 0x2154),  # PatientTelephoneNumbers
    ]
    checked = 0
    for root, _dirs, files in os.walk(dicom_dir):
        for fn in files:
            path = os.path.join(root, fn)
            try:
                ds = pydicom.dcmread(path, stop_before_pixels=True, force=True)
            except Exception:
                continue
            checked += 1

            # 匿名化マーカーがあれば合格（擬似IDが入っていても識別子ではない）
            if str(getattr(ds, "PatientIdentityRemoved", "")).strip().upper() == "YES":
                if checked >= 50:
                    log(f"PHI ガード: {checked} 枚を検査、匿名化マーカーを確認。")
                    return
                continue

            for tag in phi_tags:
                if tag in ds and str(ds[tag].value).strip():
                    raise PHIGuardError(
                        "入力が匿名化されていません。先に anonymize_dicom.py を実行してください。"
                        "（どのタグかはセキュリティのため非表示）"
                    )
            if checked >= 50:  # 先頭 50 枚で十分
                log(f"PHI ガード: {checked} 枚を検査、識別子タグなしを確認。")
                return
    log(f"PHI ガード: {checked} 枚を検査、未匿名化の識別子タグなしを確認。")


# --------------------------------------------------------------------------- #
# 各工程
# --------------------------------------------------------------------------- #
def run_command(cmd: Iterable[str], env: dict[str, str], log: Log) -> None:
    cmd = list(cmd)
    log(f"実行: {cmd[0]} ...（引数省略）")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        log(line.rstrip())
    proc.wait()
    if proc.returncode != 0:
        raise PipelineError(f"コマンドが異常終了しました（code={proc.returncode}）。")


def step_dicom_to_nifti(
    dcm2niix: str, dicom_dir: str, nifti_dir: str, log: Log
) -> str:
    log("--- ステップ1: DICOM → NIfTI ---")
    # -d 9: サブフォルダを 9 階層まで探索 / -i n: 派生画像も含める / -z y: gzip
    cmd = [dcm2niix, "-o", nifti_dir, "-z", "y", "-d", "9", "-i", "n", dicom_dir]
    run_command(cmd, os.environ.copy(), log)

    produced = [f for f in os.listdir(nifti_dir) if f.endswith(".nii.gz")]
    if len(produced) != 1:
        raise PipelineError(
            f"NIfTI が 1 つではありません（{len(produced)} 個）。DICOM フォルダ構成を確認してください。"
        )
    case = os.path.join(nifti_dir, "case_0000.nii.gz")
    os.rename(os.path.join(nifti_dir, produced[0]), case)
    log("ステップ1 完了")
    return case


def step_segmentation(
    nifti_dir: str,
    seg_dir: str,
    *,
    dataset: str,
    configuration: str,
    fold: str,
    device: str,
    model_dir: str,
    log: Log,
) -> None:
    log("--- ステップ2: nnU-Netv2 セグメンテーション ---")
    model_root = os.path.join(
        model_dir, dataset, f"nnUNetTrainer__nnUNetPlans__{configuration}"
    )
    if not os.path.isdir(model_root):
        raise PipelineError(
            f"学習モデルが見つかりません: {model_root}\n"
            f"各自で {dataset} の学習モデルを {model_dir} 配下に配置してください"
            f"（references/model_acquisition.md 参照）。"
        )

    env = os.environ.copy()
    env["nnUNet_results"] = model_dir
    # raw / preprocessed は推論では使わないが、未設定だと nnunetv2 が警告するため一時パスを与える
    env.setdefault("nnUNet_raw", os.path.join(tempfile.gettempdir(), "nnUNet_raw"))
    env.setdefault(
        "nnUNet_preprocessed",
        os.path.join(tempfile.gettempdir(), "nnUNet_preprocessed"),
    )
    os.makedirs(env["nnUNet_raw"], exist_ok=True)
    os.makedirs(env["nnUNet_preprocessed"], exist_ok=True)

    # 推論を高速化: TTA 無効 + step_size 0.7（精度への影響は軽微）
    common = [
        "-i", nifti_dir, "-o", seg_dir,
        "-d", dataset, "-c", configuration, "-f", fold,
        "-device", device, "--disable_tta", "-step_size", "0.7",
    ]
    exe = shutil.which("nnUNetv2_predict")
    if exe:
        run_command([exe, *common], env, log)
    else:
        log("nnUNetv2_predict が無いため python -m にフォールバックします。")
        run_command(
            [sys.executable, "-m", "nnunetv2.inference.predict", *common], env, log
        )
    log("ステップ2 完了")


def step_nifti_to_stl(seg_dir: str, out_dir: str, log: Log) -> list[str]:
    log("--- ステップ3: NIfTI → STL ---")
    from nifti_to_stl import nifti_to_stl  # 重い依存は実行時に読み込む

    seg_files = [f for f in os.listdir(seg_dir) if f.endswith(".nii.gz")]
    if not seg_files:
        raise PipelineError("セグメンテーション結果（.nii.gz）が見つかりません。")
    seg_path = os.path.join(seg_dir, sorted(seg_files)[0])
    written = nifti_to_stl(seg_path, out_dir, TARGET_LABELS)
    log(f"ステップ3 完了: {len(written)} 個の STL を出力")
    return written


# --------------------------------------------------------------------------- #
# オーケストレーション
# --------------------------------------------------------------------------- #
def run(
    dicom_dir: str,
    out_dir: str,
    *,
    model_dir: str,
    device: str,
    dataset: str,
    configuration: str,
    fold: str,
    dcm2niix: Optional[str],
    require_anonymized: bool,
    log: Log = _log,
) -> list[str]:
    if not os.path.isdir(dicom_dir):
        raise PipelineError(f"入力フォルダが存在しません: {dicom_dir}")
    os.makedirs(out_dir, exist_ok=True)

    if require_anonymized:
        assert_anonymized(dicom_dir, log)

    dcm2niix_path = resolve_dcm2niix(dcm2niix)
    resolved_device = detect_device(device, log)

    case_name = os.path.basename(os.path.normpath(dicom_dir))
    case_out = os.path.join(out_dir, f"{case_name}_stl")
    os.makedirs(case_out, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        nifti_dir = os.path.join(tmp, "nifti")
        seg_dir = os.path.join(tmp, "seg")
        os.makedirs(nifti_dir, exist_ok=True)
        os.makedirs(seg_dir, exist_ok=True)

        step_dicom_to_nifti(dcm2niix_path, dicom_dir, nifti_dir, log)
        step_segmentation(
            nifti_dir,
            seg_dir,
            dataset=dataset,
            configuration=configuration,
            fold=fold,
            device=resolved_device,
            model_dir=model_dir,
            log=log,
        )
        written = step_nifti_to_stl(seg_dir, case_out, log)

    # 出力はファイル名のみ報告（PHI を含まない命名）
    log("=== 完了: 出力 STL ===")
    for path in written:
        log(f"  {os.path.basename(path)}")
    return written


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="DICOM → NIfTI → nnU-Netv2 → STL ヘッドレス・パイプライン"
    )
    p.add_argument("--in", dest="dicom_dir", required=True, help="入力 DICOM フォルダ")
    p.add_argument("--out", dest="out_dir", required=True, help="STL 出力フォルダ")
    p.add_argument(
        "--model-dir",
        default=os.environ.get("NNUNET_RESULTS_DIR", "./nnUNet_results"),
        help="学習モデルを置いた nnUNet_results ディレクトリ（各自で配置）",
    )
    p.add_argument(
        "--device", default="auto", choices=["auto", "cpu", "cuda", "mps"]
    )
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--configuration", default=DEFAULT_CONFIGURATION)
    p.add_argument("--fold", default=DEFAULT_FOLD)
    p.add_argument("--dcm2niix", default=None, help="dcm2niix 実行ファイルの明示パス")
    p.add_argument(
        "--require-anonymized",
        action="store_true",
        help="入力に PHI タグが残っていれば実行を拒否する（院外共有時に推奨）",
    )
    p.add_argument(
        "--accept-disclaimer",
        action="store_true",
        help="研究用途・免責事項に同意した場合に指定",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    if not args.accept_disclaimer:
        print(DISCLAIMER, file=sys.stderr)
        return 1
    try:
        run(
            args.dicom_dir,
            args.out_dir,
            model_dir=args.model_dir,
            device=args.device,
            dataset=args.dataset,
            configuration=args.configuration,
            fold=args.fold,
            dcm2niix=args.dcm2niix,
            require_anonymized=args.require_anonymized,
        )
    except PHIGuardError as exc:
        print(f"PHI ガード違反: {exc}", file=sys.stderr)
        return 3
    except PipelineError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"想定外のエラー: {exc}", file=sys.stderr)
        return 99
    return 0


if __name__ == "__main__":
    sys.exit(main())
