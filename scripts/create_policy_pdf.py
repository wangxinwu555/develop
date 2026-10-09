"""把模拟政策生成为可供 PyPDFLoader 读取的 PDF；开发环境使用。"""

import json
from html import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from supportflow.config import ROOT


def main():
    policies = json.loads((ROOT / "data/policies.json").read_text(encoding="utf-8"))
    path = ROOT / "output/pdf/售后政策.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    title = ParagraphStyle(
        "title",
        fontName="STSong-Light",
        fontSize=22,
        leading=32,
        textColor=colors.HexColor("#173c5c"),
        spaceAfter=22,
    )
    body = ParagraphStyle(
        "body", fontName="STSong-Light", fontSize=13, leading=24, wordWrap="CJK", spaceAfter=12
    )
    marker = ParagraphStyle(
        "marker", fontName="Helvetica", fontSize=9, leading=14, textColor=colors.HexColor("#667788")
    )
    story = []
    for number, policy in enumerate(policies):
        if number:
            story.append(PageBreak())
        story.extend(
            [
                Paragraph(escape(policy["title"]), title),
                Paragraph(f"POLICY_ID: {policy['id']}", marker),
                Spacer(1, 24),
            ]
        )
        for sentence in policy["text"].split("。"):
            if sentence:
                story.append(Paragraph(escape(sentence + "。"), body))

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.HexColor("#667788"))
        canvas.drawString(56, 36, f"SupportFlow | Fictional demo policies | Page {doc.page}")

    SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=56,
        rightMargin=56,
        topMargin=64,
        bottomMargin=60,
        invariant=1,
        title="SupportFlow demo policies",
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    print(path)


if __name__ == "__main__":
    main()
