---
name: "arch-hub"
description: "プロジェクトが一区切り／クローズした段階で、構造を棚卸ししてレポート化・JSON化し、PenClawアーキテクチャ俯瞰図に統合して資産に変える工程。「プロジェクトを閉じる」「棚卸しして」「俯瞰図に追加」「アーキテクチャに追加」「arch-hub」「構造をまとめて」「レポートにして」「資産化」と言われたら発動。"
---

# arch-hub — プロジェクトを閉じて資産に変える

セッションは使い捨てだが、構造の理解は残せる。プロジェクトが一区切りしたら、**実地調査 → レポート化 → JSON化 → 俯瞰図に統合**を通し、次のエージェントと先生の両方が読める資産にする。

## 成果物は3つ

| 層 | 成果物 | 読む相手 | 場所 |
|---|---|---|---|
| 詳細層 | `architecture.json` | エージェント | プロジェクト直下の `architecture/` |
| 詳細層 | `architecture.html` | 先生 | 同上（JSONから生成） |
| 俯瞰層 | `penclaw_arch.json` のビュー1件 | 両方 | `司令室/architecture_hub/` |

**役割が違う。** 俯瞰層は「流れ」を見るため、詳細層は「改修」のため。エージェントに直させるときは詳細層を読ませる。

## 正本の場所

| 役割 | パス |
|---|---|
| 俯瞰データ正本 | `PenClaw司令室/architecture_hub/penclaw_arch.json` |
| 俯瞰レンダラ | `PenClaw司令室/architecture_hub/build_hub.py` |
| 詳細レンダラ | `PenClaw司令室/patient_chat/architecture/build_html.py`（コピーして使う） |
| 詳細層の作り方 | `PenClaw司令室/patient_chat/architecture/README.md` |
| Coworkアーティファクト | id `penclaw-architecture-flows` |
| 棚卸し索引 | `PenClaw司令室/アーキテクチャ俯瞰_index.html` |

**HTMLを直接編集しない。** JSONを直して再生成する。両者が乖離しないのはこの一方通行を守っているから。

---

# 工程

## 工程1 — 実地調査（推測で書かない）

**確認していないことは書かず、`open_questions` に「未確認」と記す。** ここで嘘を書くと、以後すべての判断が汚染される。

- コードは Read、DBは実際にクエリ、本番はキャッシュ回避（`?cb=<タイムスタンプ>`）で取得
- 日付は必ず `TZ=Asia/Tokyo date "+%Y-%m-%d"` で実日を取る（コンテキスト表示は3日ズレた実例あり）
- **マウント上で git コマンドを実行しない。** index.lock が残置され、サンドボックスからは削除できず先生の git が全滅する
- 規模が大きければ Agent（general-purpose）に**読み取り専用**で調査を投げ、報告を受けてから自分でJSONを書く。その際は書き込み禁止・git禁止・秘匿値を報告に含めないことを明示する

### 特に探すもの — ズレの兆候

ここが本工程の中心。**きれいな構造図を描くことではなく、本番と正本の食い違いを見つけることが目的**。

- 本番と正本の内容差異（行単位ハッシュで突合。後述）
- 本番にしか実体が無いコード（`local_path: null` になるもの）
- ドキュメントの記述と実装の乖離（API一覧・環境変数・手順書のパス）
- 同じ処理の重複実装（数値や色の意味が食い違っていないか）
- 秘匿値の平文保管（トークン・APIキー・通知トピック名）
- 公開物へのPII混入（実名・個人メール）
- 配布物とローカル正本のバージョン差

## 工程2 — 本番と正本の突合（行単位ハッシュ）

**本文を目視・手作業で転記しない。** 長文（特に日本語）の再転記は文字化け・誤字混入を起こす（キリル文字混入、デプロイのバンドル失敗の実例あり）。

正本側で行ごとのハッシュを出す。

```bash
python3 - <<'PY'
import hashlib, json
lines = open('<正本ファイル>', encoding='utf-8').read().split('\n')
print(json.dumps([hashlib.sha256(l.encode()).hexdigest()[:10] for l in lines]))
PY
```

本番側（ブラウザ）で同じ配列を作り、突き合わせる。

```javascript
const LOCAL = [/* 上の出力 */];
const t = await fetch(URL + '?cb=' + Date.now()).then(r => r.text());
const L = t.split('\n');
async function h(s){ const b = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return Array.from(new Uint8Array(b)).map(x => x.toString(16).padStart(2,'0')).join('').slice(0,10); }
const H = []; for (const l of L) H.push(await h(l));
const localSet = new Set(LOCAL), liveSet = new Set(H);
JSON.stringify({
  liveLines: H.length, localLines: LOCAL.length,
  liveOnly: H.map((x,i)=>i+1).filter((_,i)=>!localSet.has(H[i])),
  localOnly: LOCAL.map((x,i)=>i+1).filter((_,i)=>!liveSet.has(LOCAL[i]))
})
```

**読み方が肝心。** `localOnly` が0件なら「本番は正本の全行を含む＝純粋な追記」と確定する。この場合は追記ブロックを足すだけで安全に反流できる。0件でなければ既存行の改変を含むので、手作業に回す。

反流後は全行ハッシュの完全一致（不一致0件）まで確認する。

**注意:** ブラウザの安全フィルタが query string を含む内容の返却をブロックすることがある。迂回しない。取得だけ先生に依頼し、差分の位置と性質はハッシュで特定して伝える。

## 工程3 — 詳細層をつくる

`architecture/` を作り、`build_html.py` をコピーして `architecture.json` を書く。スキーマは `penclaw-arch/1.0`。

必須キー：

| キー | 内容 |
|---|---|
| `meta` | project / title / generated_at（実日）/ status / **source_of_truth**（正本ファイルのパス群）/ related_memory |
| `platform` | 稼働基盤の前提（プロジェクトref・スキーマ・ホスト・WAF等） |
| `components[]` | id / kind / name / **local_path**（本番にしか無いなら null を明示）/ deployed_path / responsibilities / constants / key_functions / deployment |
| `gate_pipeline[]` | 分岐ロジックがあるなら order / trigger / action の順序付き配列 |
| `invariants[]` | 勝手に変えてはいけない設計判断。**なぜそうなっているかまで書く** |
| `do_not_touch[]` | target / reason。事故に直結する箇所 |
| `known_drift[]` | severity(high\|medium\|low) / title / detail / impact / action / verified_at |
| `gotchas[]` | 実際に踏んだ落とし穴のみ。一般論・予想は書かない |
| `change_recipes[]` | task / steps[]。よくある改修の手順 |
| `open_questions[]` | 未確認・宿題 |

**数値には `_at_generation` 接尾辞を付ける**（`counts_at_generation` 等）。スナップショットであることを明示しないと、次に読むエージェントが古い数字を現在値と誤認する。

生成：`python3 build_html.py`

## 工程4 — 俯瞰層に統合する

`penclaw_arch.json` の `views` に1件足す。

```json
{
  "id": "kebab-case-id",
  "label": "タブに出す短い名前",
  "desc": "1行の説明",
  "status": "本番稼働中（2026-XX-XX〜）",
  "drift": { "high": 0, "medium": 0, "low": 0 },
  "detail_json": "詳細層のarchitecture.jsonのパス",
  "links": [
    { "label": "棚卸しレポート", "href": "相対パス", "path": "表示・コピー用のパス" }
  ],
  "columns": [ { "id": "actors", "label": "ACTORS" } ],
  "nodes": [
    { "id": "一意ID", "col": "列ID", "kind": "種別", "title": "名前", "sub": "補足",
      "view": "（任意）ドリルダウン先のビューID",
      "anchor": true, "anchor_why": "（anchorのときだけ）なぜ議論できないのか",
      "tier": "L1|L2|L3（agent種別のみ・D-026）" }
  ],
  "edges": [
    { "from": "ノードID", "to": "ノードID", "kind": "data",
      "carries": "実際に渡るもの", "source": "flows" }
  ],
  "flows": [
    { "id": "一意ID", "title": "フロー名", "desc": "1行説明",
      "steps": [ { "from": "ノードID", "to": "ノードID", "label": "何をするか",
                   "detail": "何が渡されるか・数値・注意点" } ] }
  ]
}
```

`kind` は `actor` / `agent` / `client` / `function` / `data` / `pipeline` / `dist` / `external`。

### edges — 依存を構造として持つ（D-055）

`flows` は「人が実際にやること」の記述で、`edges` は「何が何を待つか」の構造。**役割が違うので両方要る。** flows だけだと、並列化できる箇所と、並列にすると壊れる箇所が読めない。

| `kind` | 意味 | `carries` | 描画 |
|---|---|---|---|
| `data` | 実データが渡る本物の依存 | 必須 | 黄の実線 |
| `order` | データは運ばないが順序自体に意味がある | `null` | 灰の破線 |
| `resource` | 同じファイル・同じレート枠を触る**隠れエッジ** | `null`。代わりに `resource` と `why` | 赤の破線 |

**data エッジは flows から機械導出する**（下記）。手で書き足すのは `order` と `resource` だけ。

```bash
python3 - <<'PY'
import json, collections
d=json.load(open('penclaw_arch.json',encoding='utf-8'))
for v in d['views']:
    agg=collections.OrderedDict()
    for f in v.get('flows',[]):
        for s in f['steps']:
            if s['from']!=s['to']:
                agg.setdefault((s['from'],s['to']),[]).append(s['label'])
    derived=[{"from":a,"to":b,"kind":"data","carries":" / ".join(dict.fromkeys(l)),"source":"flows"}
             for (a,b),l in agg.items()]
    keep=[e for e in v.get('edges',[]) if e.get('source')!='flows']   # 手書き分は温存
    v['edges']=derived+keep
json.dump(d,open('penclaw_arch.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
PY
```

**`resource` エッジがこのスキーマの主役。** プロンプト上は独立に見えて同じ資源を掴む2ノードは、並列にすると壊れる。PenClaw で実際に起きた4件（WAFのCPT REST書き込み枠・テーマ資産の `?v=` バンプ・widget本体とCV-guardパッチの同居・個人スキル空間の第2正本化）は全部これ。**書くのは事故が起きた／起きうる根拠があるものだけ**で、予想は書かない。

### anchor — 議論できない検証点

`anchor: true` は「そのノードが出すものは反論できない事実」という意味。**実際に起きたことだけ**が anchor で、宣言・カタログ・「〜したはず」は anchor ではない。

| anchor である | anchor ではない |
|---|---|
| 実機にインストールされたスキルの中身 | `marketplace.json` の version 表記 |
| 壊れ symlink を数えた結果 | glob の戻り値 |
| GA4・Ads の実測CV | 「配信したので効いているはず」 |
| `?cb=` 付き no-store fetch の実取得 | web_fetch の既定（サーバキャッシュが返る） |

**anchor が0件のビューは要注意。** ノードどうしが互いの報告を読み合っているだけで、全部の緑ランプが灯ったまま壊れうる。`check_edges.py` が警告する。

`links` の `href` は `architecture_hub/` からの相対パス。**司令室と PenClawリポは実マシン上で兄弟ではない**ので注意（サンドボックスのマウントとは深さが違う）。

- 司令室内 → `../<project>/architecture/architecture.html`
- PenClawリポ → `../../../../../Desktop/VScode/PenClaw/architecture/<name>.html`

**全体図（`overview`）にもノードを1つ足す。** `col: "projects"` に置き、`view` に新ビューのIDを入れてドリルダウンできるようにする。既存フローに絡むなら、そのステップにも登場させる。

生成：`python3 build_hub.py`

### 設計の指針（質の分かれ目）

**列は処理の流れで並べる。** 左から右へデータが流れる順。5列程度、ノードは20個以内。増えるほど読めなくなる。細かいものは `sub` に畳む。

**フローは人が実際にやることで立てる。** モジュール構成の説明ではなく「患者がチャットで質問する」「衛生士がDr.を呼ぶ」という一続きの出来事にする。1ビューに3〜6本。

**`detail` に数値と固有名詞を入れる。** 「認証する」ではなく「REG_PINを定数時間比較。IPレート制限は実質無効で、防御はPINのエントロピー単独」と書く。ここが読む価値の中心。

**危険な箇所は `**強調**` で書く。** レンダラが黄色の太字にする。事故につながる箇所・止まっている経路に使う。

**自己ループ（`from` と `to` が同じ）を使ってよい。** 内部処理の段を表現できる。同一ノードで複数回使うと弧が自動で広がる。

## 工程5 — 機械検証（省略しない）

### エッジ検査（D-055）

```bash
cd <司令室>/architecture_hub && python3 check_edges.py
```

4つを見る。参照整合性、fake edge（`kind: data` なのに `carries` が空＝待つ理由が無い）、anchor の不在、隠れエッジの列挙。最後に**並列化の候補**（同じ後段に入り互いに依存の無い実行ノードの組）を出す。

**候補の読み方が肝心。** 後段が `data` / `external` の組は、並列化できるサインではなく**隠れエッジの疑い**。同じテーブル・同じAPIに書き込んでいる可能性がある。実装を読んで確かめ、競合するなら `resource` エッジを足す。1ホップ先しか見ていないので、間接依存のある組も候補に出る。目視で落とす。

### 参照の健全性

```bash
cd <司令室>/architecture_hub
python3 - <<'PY'
import json
d=json.load(open('penclaw_arch.json',encoding='utf-8'))
bad=0
vids={x['id'] for x in d['views']}
for v in d['views']:
    ids={n['id'] for n in v['nodes']}; cols={c['id'] for c in v['columns']}
    for n in v['nodes']:
        if n['col'] not in cols: print('MISS col',v['id'],n['id']); bad+=1
        if n['kind'] not in d['kinds']: print('MISS kind',v['id'],n['id']); bad+=1
        if n.get('view') and n['view'] not in vids: print('MISS drill',v['id'],n['id']); bad+=1
    for f in v['flows']:
        for i,s in enumerate(f['steps'],1):
            for k in ('from','to'):
                if s[k] not in ids: print('MISS node',v['id'],f['id'],i,k,s[k]); bad+=1
    print(f"{v['id']:16} nodes {len(v['nodes']):2} flows {len(v['flows'])} steps {sum(len(f['steps']) for f in v['flows'])}")
print('未解決参照:', bad)
PY
```

### 詳細層の突合（パス実在・秘匿値・HTML一致）

```bash
python3 - <<'PY'
import json, re, pathlib
base = pathlib.Path('<マウントのルート>')
raw = open('architecture.json', encoding='utf-8').read()
d = json.loads(raw)
for p in sorted(set(re.findall(r'(?:PenClaw司令室|PenClaw)/[A-Za-z0-9_./\-]+', raw))):
    print(('OK  ' if (base/p).exists() else 'MISS'), p)
for pat, label in [(r'eyJhbGciOi','JWT'), (r'AIza','GoogleAPIキー'), (r'postgres://','DB URL')]:
    print(('NG  ' if re.search(pat, raw) else 'OK  '), label)
h = open('architecture.html', encoding='utf-8').read()
emb = json.loads(re.search(r'id="arch">(.*?)</script>', h, re.S).group(1).replace('<\\/','</'))
print('HTML埋め込み一致:', emb == d)
PY
```

**注意:** 日本語を含むパスは ASCII のみの文字クラスでは途中で切れる。`MISS` が出たら、まず自分の正規表現を疑う。

### 描画チェック

`chrome-devtools` MCP で `file://` の HTML を開き、全ビュー・全フローを機械的に回す。

```javascript
() => { const res=[]; const nt=document.querySelectorAll('.tab').length;
  for(let t=0;t<nt;t++){ document.querySelectorAll('.tab')[t].click();
    const nf=document.querySelectorAll('.flow').length;
    for(let i=0;i<nf;i++){ document.querySelectorAll('.flow')[i].click();
      const b=[...document.querySelectorAll('.badge')].map(e=>[parseFloat(e.style.left),parseFloat(e.style.top)]);
      let ov=0; for(let x=0;x<b.length;x++) for(let y=x+1;y<b.length;y++)
        if(Math.abs(b[x][0]-b[y][0])<20&&Math.abs(b[x][1]-b[y][1])<20) ov++;
      const steps=document.querySelectorAll('.step').length;
      if(ov>0||b.length!==steps) res.push({v:document.querySelector('.tab.on').textContent.trim(),
        f:document.querySelector('.flow.on .ft').textContent, badges:b.length, steps, overlap:ov});
      document.querySelectorAll('.flow')[i].click(); } }
  return JSON.stringify({problems:res}); }
```

`problems` が空でなければ直す。最後にスクリーンショットを撮って目視する。

`links` の解決先も検証する。`fetch` は `file://` で CORS 遮断されるため、**解決後の絶対パス**（`new URL(href, location.href).pathname`）を取り出し、bash 側で実在確認する。

## 工程6 — 資産として残す

1. **アーティファクトを更新** — `mcp__cowork__update_artifact` に id `penclaw-architecture-flows` と生成HTMLのパスを渡す。これでセッションをまたいで最新版が開ける
2. **索引を更新** — `司令室/アーキテクチャ俯瞰_index.html` のカードとHIGH一覧に追記
3. **memory を更新** — 検出したズレの要点を `feedback_architecture_drift_findings.md` に、新しい知見があれば個別ファイルに。`MEMORY.md` に1行のポインタを足す
4. **決裁が要る事項を提示** — HIGH のズレは「事実／影響／やること」の形で先生に出し、判断を仰ぐ。**勝手に直さない**
5. **D番号で記録** — 設計判断があれば `knowledge_base/decisions.md` に。採番前後に `grep -oE "^### D-[0-9]+\b" knowledge_base/decisions.md | sort | uniq -d` で重複確認

---

# 更新のタイミング

作るのは一度、更新は下記のときだけ。**放置して古くなった俯瞰図は、無いより有害。**

| いつ | 何をする |
|---|---|
| プロジェクトが一区切り | ビューを1件追加＋詳細層を作成 |
| コンポーネントの増減 | `nodes` と `components` を更新 |
| 処理の流れが変わった | 該当 `flows` の `steps` と `gate_pipeline` を更新し、**data エッジを再導出** |
| 同じファイル・同じ枠を触る組を見つけた | `resource` エッジを足す。事故が起きる前に描くのが目的 |
| **ズレを発見** | `known_drift` に追記。**直せなくても記録する** |
| ズレを解消 | 該当エントリを削除し `drift` の件数と `generated_at` を更新 |
| 失敗して学んだ | `gotchas` に1行追記（CLAUDE.md の Gotchas と同じ流儀） |

`drift` の件数はタブの色ドットに出る。high>0 なら赤、medium>0 なら橙、どちらも0なら緑。**赤が残り続けているビューは、決裁が滞っているサイン**として先生に上げる。

# やらないこと

- HTMLを直接編集する（次の再生成で消える）
- 推測でノード・フロー・コンポーネントを書く
- 秘匿値を書く（APIキー・トークン・通知トピック名など）。「平文で存在する」という事実だけを書く
- 患者氏名・カルテ情報を書く（カルテ番号単体は D-032 により匿名コード扱いで可）
- ノードを詰め込みすぎる（20個を超えたら列の切り方を見直す）
- ズレを見つけて黙って直す（HIGH は決裁事項）
- 検証を飛ばして「できました」と報告する
- `resource` エッジを予想で書く（事故か、事故に至る根拠のあるものだけ）
- 宣言・カタログ・バージョン表記を `anchor` にする（実際に起きたことだけが anchor）

