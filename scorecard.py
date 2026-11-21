"""Downloadable PDF examination scorecard (ReportLab)."""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from models import local_now


def build_scorecard_pdf(booking):
    ev = booking.evaluation
    exam = booking.examination
    student = booking.student
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Scorecard - {exam.name}", author="Examination Management Portal")
    ss = getSampleStyleSheet()
    body = []
    body.append(Paragraph("Examination Management Portal", ss["Title"]))
    body.append(Paragraph("Official Examination Scorecard", ss["Heading2"]))
    body.append(Spacer(1, 4 * mm))

    info = [
        ["Student", student.name, "Student ID", student.student_profile.student_number if student.student_profile else "-"],
        ["Course", f"{exam.course.course_code} - {exam.course.name}", "Examination", exam.name],
        ["Type", exam.exam_type, "Slot", booking.slot.label()],
        ["Examiner", ev.examiner.name, "Booking #", str(booking.id)],
    ]
    t = Table(info, colWidths=[24 * mm, 62 * mm, 26 * mm, 62 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8ccd2")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f3f5")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f1f3f5")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    body += [t, Spacer(1, 6 * mm)]

    rows = [["Criterion", "Max", "Awarded", "Comment"]]
    for s in ev.scores:
        rows.append([Paragraph(s.criterion.criterion_name, ss["BodyText"]), str(s.criterion.maximum_marks),
                     f"{s.marks_awarded:g}", Paragraph(s.comment or "", ss["BodyText"])])
    rows.append(["Total", str(exam.maximum_marks), f"{ev.total_marks:g}", f"{ev.percentage}%"])
    st = Table(rows, colWidths=[62 * mm, 18 * mm, 22 * mm, 72 * mm], repeatRows=1)
    st.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#eef2f7")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8ccd2")),
        ("ALIGN", (1, 0), (2, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    body += [st, Spacer(1, 6 * mm)]

    verdict = "PASS" if ev.passed else "NOT PASSED"
    body.append(Paragraph(f"<b>Result:</b> {verdict} (passing marks {exam.passing_marks}/{exam.maximum_marks})",
                          ss["BodyText"]))
    if ev.remarks:
        body.append(Spacer(1, 2 * mm))
        body.append(Paragraph(f"<b>Examiner remarks:</b> {ev.remarks}", ss["BodyText"]))
    body.append(Spacer(1, 8 * mm))
    body.append(Paragraph(
        f"Evaluated on {ev.evaluated_at:%d %b %Y %H:%M} &middot; Published on {ev.published_at:%d %b %Y %H:%M}"
        f" &middot; Generated {local_now():%d %b %Y %H:%M}", ss["Italic"]))
    doc.build(body)
    return buf.getvalue()
