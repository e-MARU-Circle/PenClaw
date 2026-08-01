from manim import *

config.background_color = "#101E2E"
JP = "Noto Serif CJK JP"
TEAL = "#68AEBA"
GOLD = "#DDC26A"
ENAMEL = "#F5F7F8"
DENTIN = "#EFDFAC"
PULP = "#E58A80"
CARIES = "#4A2E1E"

def make_tooth():
    enamel = SVGMobject("/tmp/anim/tooth.svg").set_style(fill_color=ENAMEL, fill_opacity=1, stroke_color=TEAL, stroke_width=3)
    enamel.set_height(4.6)
    dentin = enamel.copy().scale(0.78).set_style(fill_color=DENTIN, fill_opacity=1, stroke_width=0)
    pulp = enamel.copy().scale(0.52).set_style(fill_color=PULP, fill_opacity=1, stroke_width=0)
    g = VGroup(enamel, dentin, pulp).move_to(ORIGIN + DOWN*0.4 + LEFT*2.2)
    return g

def stage_label(code, title, sub):
    t1 = Text(f"{code} ｜ {title}", font=JP, weight=BOLD, color=GOLD).scale(0.62)
    t2 = Text(sub, font=JP, color=WHITE).scale(0.45)
    grp = VGroup(t1, t2).arrange(DOWN, aligned_edge=LEFT, buff=0.3)
    grp.to_edge(RIGHT, buff=0.7).shift(UP*0.3)
    return grp

def caries_spot(tooth, r):
    p = tooth[0].get_top() + DOWN*0.32 + LEFT*0.55
    return Circle(radius=r, color=CARIES, fill_opacity=1, stroke_width=0).move_to(p)

class S1_Intro(Scene):
    def construct(self):
        title = Text("むし歯はどう進行する？", font=JP, weight=BOLD).scale(0.85).to_edge(UP, buff=0.5)
        title.set_color_by_gradient(TEAL, GOLD)
        tooth = make_tooth()
        self.play(Write(title), run_time=1.4)
        self.play(DrawBorderThenFill(tooth[0]), run_time=1.6)
        self.play(FadeIn(tooth[1]), FadeIn(tooth[2]), run_time=1.0)
        labels = VGroup(
            Text("エナメル質", font=JP, color=ENAMEL).scale(0.45),
            Text("象牙質", font=JP, color=DENTIN).scale(0.45),
            Text("神経（歯髄）", font=JP, color=PULP).scale(0.45),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.55).to_edge(RIGHT, buff=1.2).shift(UP*0.2)
        pts = [tooth[0].get_top()+DOWN*0.25+RIGHT*0.6, tooth[1].get_center()+UP*0.6+RIGHT*0.5, tooth[2].get_center()]
        lines = VGroup(*[Line(l.get_left()+LEFT*0.12, p, stroke_width=1.6, color=GREY_B) for l, p in zip(labels, pts)])
        self.play(FadeIn(labels), Create(lines), run_time=1.4)
        self.wait(1.2)

class S2_C1(Scene):
    def construct(self):
        tooth = make_tooth()
        self.add(tooth)
        spot = caries_spot(tooth, 0.14)
        lab = stage_label("C1", "エナメル質のむし歯", "痛みはほとんどありません")
        self.play(GrowFromCenter(spot), run_time=0.9)
        self.play(FadeIn(lab, shift=LEFT*0.3), run_time=0.9)
        self.wait(1.6)

class S3_C2(Scene):
    def construct(self):
        tooth = make_tooth()
        spot = caries_spot(tooth, 0.14)
        self.add(tooth, spot)
        lab = stage_label("C2", "象牙質まで進行", "冷たいものが しみることがあります")
        big = caries_spot(tooth, 0.42).shift(DOWN*0.18)
        self.play(Transform(spot, big), run_time=1.4)
        self.play(FadeIn(lab, shift=LEFT*0.3), run_time=0.9)
        snow = Text("❄", font=JP, color="#9AD4E8").scale(0.7).next_to(tooth[0].get_top(), UP+LEFT, buff=0.15)
        self.play(FadeIn(snow, shift=DOWN*0.2), run_time=0.6)
        self.wait(1.4)

class S4_C3(Scene):
    def construct(self):
        tooth = make_tooth()
        spot = caries_spot(tooth, 0.42).shift(DOWN*0.18)
        self.add(tooth, spot)
        lab = stage_label("C3", "神経まで到達", "ズキズキと痛みます")
        deep = caries_spot(tooth, 0.62).shift(DOWN*0.55)
        self.play(Transform(spot, deep), run_time=1.4)
        self.play(Flash(tooth[2].get_center(), color=YELLOW, line_length=0.45, num_lines=10, flash_radius=0.9), FadeIn(lab, shift=LEFT*0.3), run_time=1.0)
        self.play(Indicate(tooth[2], color=RED, scale_factor=1.15), run_time=0.9)
        self.wait(1.2)

class S5_C4(Scene):
    def construct(self):
        tooth = make_tooth()
        spot = caries_spot(tooth, 0.62).shift(DOWN*0.55)
        self.add(tooth, spot)
        lab = stage_label("C4", "歯の根だけが残った状態", "神経が死に、根の先に膿がたまることも")
        crown = VGroup(tooth[0], tooth[1], tooth[2], spot)
        self.play(crown.animate.set_opacity(0.25), FadeIn(lab, shift=LEFT*0.3), run_time=1.4)
        self.wait(1.0)
        msg = Text("早期発見のために、定期的な検診をご活用ください", font=JP, weight=BOLD).scale(0.6)
        msg.set_color_by_gradient(TEAL, GOLD)
        self.play(FadeOut(crown), FadeOut(lab), run_time=0.8)
        self.play(Write(msg), run_time=1.6)
        logo = Text("江間ファミリー歯科", font=JP, color=GREY_B).scale(0.42).next_to(msg, DOWN, buff=0.6)
        self.play(FadeIn(logo), run_time=0.7)
        self.wait(1.5)
