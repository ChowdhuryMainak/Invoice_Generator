import logging
import os
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas


def _safe_text(value, fallback="-"):
    text = str(value).strip() if value is not None else ""
    return text or fallback


def _money(value):
    return f"{float(value or 0):.2f}"


def _weight(value):
    return f"{float(value or 0):.3f}"


def _truncate(value, max_length):
    text = _safe_text(value, "")
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return text[: max_length - 3] + "..."


def _fit_font_size(text, max_width, font_name, default_size, min_size=8):
    size = default_size
    while size > min_size and pdfmetrics.stringWidth(text, font_name, size) > max_width:
        size -= 1
    return size


def _draw_text(c, x, y, text, font_name="Helvetica", font_size=10, align="left", max_width=None):
    text = _safe_text(text, "")
    if max_width:
        font_size = _fit_font_size(text, max_width, font_name, font_size)
    c.setFont(font_name, font_size)
    if align == "center":
        c.drawCentredString(x, y, text)
    elif align == "right":
        c.drawRightString(x, y, text)
    else:
        c.drawString(x, y, text)


def _wrap_text_lines(text, font_name, font_size, max_width, max_lines=2):
    words = _safe_text(text, "").split()
    if not words:
        return [""]

    lines = []
    current = ""
    index = 0
    while index < len(words):
        word = words[index]
        candidate = f"{current} {word}".strip()
        if not current or pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
            index += 1
            continue
        lines.append(current)
        current = ""
        if len(lines) == max_lines - 1:
            break

    if index < len(words):
        tail = f"{current} {' '.join(words[index:])}".strip()
        while pdfmetrics.stringWidth(tail, font_name, font_size) > max_width and len(tail) > 3:
            tail = tail[:-4].rstrip() + "..."
        lines.append(tail)
    elif current:
        lines.append(current)

    if not lines:
        lines.append("")
    return lines[:max_lines]


def _format_display_datetime(value):
    text = _safe_text(value, "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%d-%m-%Y %I:%M %p")
        except ValueError:
            continue
    return text


def _resolve_invoice_display_datetime(invoice_date, updated_at):
    invoice_text = _safe_text(invoice_date, "")
    updated_text = _safe_text(updated_at, "")
    if len(invoice_text) == 10 and len(updated_text) >= 19 and updated_text[:10] == invoice_text:
        return _format_display_datetime(updated_text)
    return _format_display_datetime(invoice_text)


def _pick_original_invoice_datetime(primary_value, fallback_value):
    primary_text = _safe_text(primary_value, "")
    fallback_text = _safe_text(fallback_value, "")
    if len(primary_text) >= 19:
        return primary_text
    if len(fallback_text) >= 19:
        return fallback_text
    return primary_text or fallback_text


def _number_to_words_upto_999(number):
    ones = [
        "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
        "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
        "Seventeen", "Eighteen", "Nineteen",
    ]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    if number == 0:
        return ""
    if number < 20:
        return ones[number]
    if number < 100:
        return (tens[number // 10] + (" " + ones[number % 10] if number % 10 else "")).strip()
    return (
        ones[number // 100]
        + " Hundred"
        + (" " + _number_to_words_upto_999(number % 100) if number % 100 else "")
    ).strip()


def _amount_in_words(value):
    amount = int(round(float(value or 0)))
    if amount == 0:
        return "Zero Rupees Only"

    parts = []
    crore = amount // 10000000
    amount %= 10000000
    lakh = amount // 100000
    amount %= 100000
    thousand = amount // 1000
    amount %= 1000
    hundred_part = amount

    if crore:
        parts.append(f"{_number_to_words_upto_999(crore)} Crore")
    if lakh:
        parts.append(f"{_number_to_words_upto_999(lakh)} Lakh")
    if thousand:
        parts.append(f"{_number_to_words_upto_999(thousand)} Thousand")
    if hundred_part:
        parts.append(_number_to_words_upto_999(hundred_part))

    return (" ".join(parts)).strip() + " Rupees Only"


def _fetch_retail_invoice_payload(conn, invoice_number):
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(settings)")
    settings_columns = {row[1] for row in cursor.fetchall()}
    has_shop_contact = "shop_contact" in settings_columns
    has_shop_gst_no = "shop_gst_no" in settings_columns
    has_shop_address = "shop_address" in settings_columns

    cursor.execute("PRAGMA table_info(invoices)")
    invoice_columns = {row[1] for row in cursor.fetchall()}
    has_updated_at = "updated_at" in invoice_columns

    cursor.execute(
        """
        SELECT shop_name, owner_name,
               {shop_contact_select},
               {shop_gst_select},
               {shop_address_select}
        FROM settings
        LIMIT 1
        """.format(
            shop_contact_select="COALESCE(shop_contact, '')" if has_shop_contact else "''",
            shop_gst_select="COALESCE(shop_gst_no, '')" if has_shop_gst_no else "''",
            shop_address_select="COALESCE(shop_address, '')" if has_shop_address else "''",
        )
    )
    shop_name, owner, shop_contact, shop_gst_no, shop_address = cursor.fetchone() or ("Shop", "Owner", "", "", "")

    cursor.execute(
        """
        SELECT customer_code, customer_name, mobile,
               product_name, quantity,
               net_weight, gross_weight,
               rate_per_gram,
               making_charges_per_gram,
               total_making_charges,
               total_rupees,
               gst_percent,
               gst_amount,
               final_total,
               amount_paid,
               remaining_balance,
               category,
               COALESCE(invoice_date, '')
        FROM invoice_details
        WHERE invoice_number = ?
        ORDER BY id
        """,
        (invoice_number,),
    )
    rows = cursor.fetchall()
    if not rows:
        raise Exception("No invoice details found to print.")

    cursor.execute(
        """
        SELECT invoice_date,
               items_total,
               exchange_total,
               old_balance_included,
               grand_total,
               amount_paid,
               remaining_balance,
               {updated_at_select},
               customer_code
        FROM invoices
        WHERE invoice_number = ?
        LIMIT 1
        """.format(updated_at_select="updated_at" if has_updated_at else "invoice_date"),
        (invoice_number,),
    )
    invoice_row = cursor.fetchone()

    cursor.execute(
        """
        SELECT item_description,
               net_weight,
               purity_percent,
               rate_per_gram,
               exchange_amount
        FROM exchange_details
        WHERE invoice_number = ?
        ORDER BY id
        """,
        (invoice_number,),
    )
    exchange_rows = cursor.fetchall()

    customer_code = invoice_row[8] if invoice_row else rows[0][0]
    cursor.execute(
        """
        SELECT COALESCE(city, '')
        FROM customers
        WHERE customer_code = ?
        LIMIT 1
        """,
        (customer_code,),
    )
    city_row = cursor.fetchone()

    customer_name = rows[0][1]
    mobile = rows[0][2]
    invoice_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    items_total = 0
    total_quantity = 0
    total_net_weight = 0
    total_gross_weight = 0
    total_base_total = 0
    total_gst_amount = 0
    total_final_total = 0

    for row in rows:
        qty = float(row[4] or 0)
        net_wt = float(row[5] or 0)
        gross_wt = float(row[6] or 0)
        total_rupees = float(row[10] or 0)
        gst_amount = float(row[12] or 0)
        final_total = float(row[13] or 0)

        total_quantity += qty
        total_net_weight += net_wt * qty
        total_gross_weight += gross_wt * qty
        total_base_total += total_rupees
        total_gst_amount += gst_amount
        total_final_total += final_total
        items_total += final_total

    exchange_total = sum(float(row[4] or 0) for row in exchange_rows)
    if invoice_row:
        items_total = float(invoice_row[1] or items_total)
        exchange_total = float(invoice_row[2] or exchange_total)
        old_balance = float(invoice_row[3] or 0)
        grand_total = float(invoice_row[4] or (items_total + old_balance - exchange_total))
        amount_paid = float(invoice_row[5] or rows[0][14] or 0)
        remaining_balance = float(invoice_row[6] or rows[0][15] or 0)
        updated_at = invoice_row[7] or invoice_row[0] or invoice_date
        invoice_date_value = _pick_original_invoice_datetime(rows[0][17], invoice_row[0] or invoice_date)
    else:
        amount_paid = float(rows[0][14] or 0)
        remaining_balance = float(rows[0][15] or 0)
        grand_total = amount_paid + remaining_balance
        old_balance = grand_total - items_total + exchange_total
        updated_at = invoice_date
        invoice_date_value = _pick_original_invoice_datetime(rows[0][17], invoice_date)

    return {
        "shop_name": shop_name,
        "owner": owner,
        "shop_contact": shop_contact,
        "shop_gst_no": shop_gst_no,
        "shop_address": shop_address,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date_value,
        "updated_at": updated_at,
        "invoice_date_display": _resolve_invoice_display_datetime(invoice_date_value, updated_at),
        "updated_at_display": _format_display_datetime(updated_at),
        "customer_name": customer_name,
        "mobile": mobile,
        "city": city_row[0] if city_row and city_row[0] else "",
        "items": rows,
        "exchange_rows": exchange_rows,
        "items_total": items_total,
        "exchange_total": exchange_total,
        "old_balance": old_balance,
        "grand_total": grand_total,
        "amount_paid": amount_paid,
        "remaining_balance": remaining_balance,
        "total_quantity": total_quantity,
        "total_net_weight": total_net_weight,
        "total_gross_weight": total_gross_weight,
        "total_base_total": total_base_total,
        "total_gst_amount": total_gst_amount,
        "total_final_total": total_final_total,
        "amount_in_words": _amount_in_words(grand_total),
    }


def _draw_label_value_line(c, x, y, label, value, line_start, line_end, label_font="Helvetica-Bold", value_font="Helvetica"):
    _draw_text(c, x, y, label, label_font, 9)
    _draw_text(c, line_start, y, _safe_text(value), value_font, 8.5, max_width=line_end - line_start)


def _label_value_start(x, label, font_name="Helvetica-Bold", font_size=9, gap=8):
    return x + pdfmetrics.stringWidth(label, font_name, font_size) + gap


def _write_retail_invoice_pdf(payload):
    pdf_dir = os.path.join("Invoices", "RetailPDF")
    os.makedirs(pdf_dir, exist_ok=True)
    pdf_path = os.path.join(pdf_dir, f"invoice_{payload['invoice_number']}.pdf")

    page_width = A4[0]
    left = 28
    right = page_width - 28
    top_margin = 20
    bottom_margin = 20

    row_count = max(len(payload["items"]), 5)
    row_height = 18
    table_header_height = 26
    totals_height = 24
    body_height = row_count * row_height
    lower_height = 190
    content_height = 78 + 68 + 12 + table_header_height + body_height + totals_height + lower_height
    page_height = max(A4[1], content_height + top_margin + bottom_margin)

    c = canvas.Canvas(pdf_path, pagesize=(page_width, page_height))
    top = page_height - top_margin
    outer_bottom = top - content_height

    c.setLineWidth(1.8)
    c.rect(left, outer_bottom, right - left, top - outer_bottom)

    header_bottom = top - 78
    customer_bottom = header_bottom - 68
    table_top = customer_bottom - 10
    table_header_bottom = table_top - table_header_height
    table_bottom = table_header_bottom - body_height
    totals_bottom = table_bottom - totals_height
    lower_top = totals_bottom - 10
    lower_bottom = outer_bottom

    for y in [header_bottom, customer_bottom, table_top, table_header_bottom, table_bottom, totals_bottom]:
        c.line(left, y, right, y)

    header_left_label_x = left + 10
    header_left_value_end = left + 190
    right_label_x = right - 172
    right_value_end = right - 12
    header_center_left = header_left_value_end + 10
    header_center_right = right_label_x - 10
    mid_x = (header_center_left + header_center_right) / 2

    _draw_text(
        c,
        mid_x,
        top - 24,
        payload["shop_name"],
        "Times-Bold",
        17,
        "center",
        header_center_right - header_center_left,
    )
    _draw_text(c, mid_x, top - 48, "||SHREE||", "Times-Bold", 11, "center")

    _draw_label_value_line(
        c, header_left_label_x, top - 22, "Owner Name:-", payload["owner"],
        _label_value_start(header_left_label_x, "Owner Name:-"), header_left_value_end
    )
    _draw_label_value_line(
        c, header_left_label_x, top - 56, "Address:-", payload["shop_address"],
        _label_value_start(header_left_label_x, "Address:-"), header_left_value_end
    )
    _draw_label_value_line(
        c, right_label_x, top - 22, "Contact No:-", payload["shop_contact"],
        _label_value_start(right_label_x, "Contact No:-"), right_value_end
    )
    _draw_label_value_line(
        c, right_label_x, top - 56, "GSTIN:-", payload["shop_gst_no"],
        _label_value_start(right_label_x, "GSTIN:-"), right_value_end
    )

    customer_mid = left + (right - left) * 0.70
    customer_left_label_x = left + 10
    customer_right_label_x = customer_mid + 10
    _draw_label_value_line(
        c, customer_left_label_x, header_bottom - 18, "Name:-", payload["customer_name"],
        _label_value_start(customer_left_label_x, "Name:-"), customer_mid - 10
    )
    _draw_label_value_line(
        c, customer_left_label_x, header_bottom - 40, "City:-", payload["city"],
        _label_value_start(customer_left_label_x, "City:-"), customer_mid - 10
    )
    _draw_label_value_line(
        c, customer_left_label_x, header_bottom - 62, "Number:-", payload["mobile"],
        _label_value_start(customer_left_label_x, "Number:-"), customer_mid - 10
    )
    _draw_label_value_line(
        c, customer_right_label_x, header_bottom - 18, "Invoice Number:-", payload["invoice_number"],
        _label_value_start(customer_right_label_x, "Invoice Number:-"), right - 12
    )
    _draw_label_value_line(
        c, customer_right_label_x, header_bottom - 48, "Date:-", payload["invoice_date_display"],
        _label_value_start(customer_right_label_x, "Date:-"), right - 12
    )

    column_specs = [
        ("Category", 40),
        ("Product Name", 66),
        ("Quantity", 48),
        ("Net Wt.", 48),
        ("Gross Wt.", 50),
        ("Rate", 55),
        ("Base Total", 62),
        ("GST %", 44),
        ("GST Amount", 52),
        ("Final Total", 62),
    ]
    table_x = [left + 10]
    for _, width in column_specs:
        table_x.append(table_x[-1] + width)

    for x in table_x:
        c.line(x, table_top, x, totals_bottom)

    header_y = table_top - 17
    for index, (label, _) in enumerate(column_specs):
        x1 = table_x[index]
        x2 = table_x[index + 1]
        font_size = 8 if label in {"Category", "Quantity", "GST Amount"} else 8.5
        _draw_text(c, (x1 + x2) / 2, header_y, label, "Helvetica-Bold", font_size, "center", x2 - x1 - 6)

    body_top = table_header_bottom - 13
    for index in range(row_count):
        y = body_top - index * row_height
        if index < len(payload["items"]):
            item = payload["items"][index]
            qty = float(item[4] or 0)
            values = [
                _safe_text(item[16], ""),
                _safe_text(item[3], ""),
                f"{int(qty) if qty.is_integer() else qty:g}",
                _weight((item[5] or 0) * qty),
                _weight((item[6] or 0) * qty),
                _money(item[7]),
                _money(item[10]),
                _money(item[11]),
                _money(item[12]),
                _money(item[13]),
            ]
        else:
            values = [""] * len(column_specs)

        for col_index, value in enumerate(values):
            x1 = table_x[col_index]
            x2 = table_x[col_index + 1]
            align = "left" if col_index in {0, 1} else "center"
            x = x1 + 4 if align == "left" else (x1 + x2) / 2
            _draw_text(c, x, y, value, "Helvetica", 8.3, align, x2 - x1 - 8)

    totals_y = table_bottom - 16
    totals_values = [
        "Totals:-",
        "",
        f"{int(payload['total_quantity']) if float(payload['total_quantity']).is_integer() else payload['total_quantity']:g}",
        _weight(payload["total_net_weight"]),
        _weight(payload["total_gross_weight"]),
        "",
        _money(payload["total_base_total"]),
        "",
        _money(payload["total_gst_amount"]),
        _money(payload["total_final_total"]),
    ]
    for index, value in enumerate(totals_values):
        x1 = table_x[index]
        x2 = table_x[index + 1]
        align = "left" if index == 0 else "center"
        x = x1 + 4 if align == "left" else (x1 + x2) / 2
        _draw_text(c, x, totals_y, value, "Helvetica-Bold", 8.5, align, x2 - x1 - 8)

    lower_mid = left + (right - left) * 0.47
    summary_label_x = lower_mid
    summary_value_x = lower_mid + 118
    c.line(left, lower_top, right, lower_top)
    c.line(lower_mid, lower_bottom, lower_mid, lower_top)
    c.line(summary_value_x, lower_bottom, summary_value_x, lower_top)

    amount_bottom = lower_top - 56
    c.line(left, amount_bottom, lower_mid, amount_bottom)
    _draw_text(c, left + 8, lower_top - 18, "Amount in Words:-", "Helvetica-Bold", 9)
    wrapped_amount = _wrap_text_lines(payload["amount_in_words"], "Helvetica", 8.5, lower_mid - left - 24, max_lines=2)
    for index, line in enumerate(wrapped_amount):
        _draw_text(c, left + 10, lower_top - 34 - (index * 13), line, "Helvetica", 8.5)

    _draw_text(c, left + 8, amount_bottom - 20, "Narration:-", "Helvetica-Bold", 9)
    _draw_text(
        c,
        left + 8,
        lower_bottom + 34,
        f"Updated at:- {payload['updated_at_display']}",
        "Helvetica",
        8.5,
        max_width=lower_mid - left - 16,
    )

    summary_rows = [
        ("Items Total:-", _money(payload["items_total"])),
        ("Exchange Less:-", _money(payload["exchange_total"])),
        ("Old Balance Include:-", _money(payload["old_balance"])),
        ("Grand Total:-", _money(payload["grand_total"])),
        ("Amount Paid:-", _money(payload["amount_paid"])),
        ("Remaining Balance:-", _money(payload["remaining_balance"])),
    ]
    row_gap = 28
    current_y = lower_top - 18
    for label, value in summary_rows:
        _draw_text(c, summary_label_x + 8, current_y, label, "Helvetica-Bold", 8.8, max_width=summary_value_x - summary_label_x - 12)
        _draw_text(c, summary_value_x + 6, current_y, value, "Helvetica", 8.6, max_width=right - summary_value_x - 14)
        current_y -= row_gap

    _draw_text(c, left + ((lower_mid - left) / 2), lower_bottom + 18, "Thanks Do Visit Again :)", "Times-Bold", 10, "center")

    c.save()
    return pdf_path


def print_invoice_for(conn, invoice_number, send_to_printer=True):
    try:
        payload = _fetch_retail_invoice_payload(conn, invoice_number)
        return _write_retail_invoice_pdf(payload)

    except Exception as exc:
        logging.error(f"Error in print_invoice_for: {exc}")
        raise


def _fetch_business_invoice_payload(conn, invoice_number):
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(settings)")
    settings_columns = {row[1] for row in cursor.fetchall()}
    has_shop_contact = "shop_contact" in settings_columns
    has_shop_address = "shop_address" in settings_columns
    has_shop_gst_no = "shop_gst_no" in settings_columns

    cursor.execute(
        """
        SELECT shop_name, owner_name,
               {shop_contact_select},
               {shop_address_select},
               {shop_gst_select}
        FROM settings
        LIMIT 1
        """.format(
            shop_contact_select="COALESCE(shop_contact, '')" if has_shop_contact else "''",
            shop_address_select="COALESCE(shop_address, '')" if has_shop_address else "''",
            shop_gst_select="COALESCE(shop_gst_no, '')" if has_shop_gst_no else "''",
        )
    )
    shop_name, owner, shop_contact, shop_address, shop_gst_no = cursor.fetchone() or ("Shop", "Owner", "", "", "")

    cursor.execute(
        """
        SELECT customer_name, mobile, product_name, net_weight, gross_weight,
               purity, wastage_percent, labour, rate_per_gram, fine_24k,
               total_rate, gst_percent, gst_amount, final_price,
               COALESCE(invoice_date, '')
        FROM business_invoice_details
        WHERE invoice_number = ?
        ORDER BY id
        """,
        (invoice_number,),
    )
    items = cursor.fetchall()

    cursor.execute(
        """
        SELECT product_name, net_weight, purity, fine_24k
        FROM business_exchange_details
        WHERE invoice_number = ?
        ORDER BY id
        """,
        (invoice_number,),
    )
    exchanges = cursor.fetchall()

    cursor.execute(
        """
        SELECT invoice_date, items_total, grand_total, amount_paid, remaining_balance,
               old_balance_included, business_items_fine, business_exchange_fine,
               from_last_invoice_fine, carry_forward_fine, updated_at,
               COALESCE(payment_mode, 'Price'),
               COALESCE(paid_fine_24k, 0),
               COALESCE(paid_price_equivalent, 0)
        FROM invoices
        WHERE invoice_number = ?
        LIMIT 1
        """,
        (invoice_number,),
    )
    invoice_row = cursor.fetchone()

    cursor.execute(
        """
        SELECT COALESCE(c.name, ''), COALESCE(c.mobile, ''), COALESCE(c.city, '')
        FROM invoices i
        LEFT JOIN customers c ON c.customer_code = i.customer_code
        WHERE i.invoice_number = ?
        LIMIT 1
        """,
        (invoice_number,),
    )
    customer_row = cursor.fetchone()

    if not items and not exchanges and not invoice_row:
        raise Exception("No business invoice data found to print.")

    invoice_date = invoice_row[0] if invoice_row and invoice_row[0] else datetime.now().strftime("%Y-%m-%d")
    updated_at = invoice_row[10] if invoice_row and invoice_row[10] else invoice_date
    if items:
        invoice_date = _pick_original_invoice_datetime(items[0][14], invoice_date)
    computed_items_total = sum(float(row[13] or 0) for row in items)
    items_total = float(invoice_row[1] or computed_items_total) if invoice_row else computed_items_total
    grand_total = float(invoice_row[2] or items_total) if invoice_row else items_total
    total_net_weight = sum(float(row[3] or 0) for row in items)
    total_gross_weight = sum(float(row[4] or 0) for row in items)
    payment_mode = str(invoice_row[11] or "Price") if invoice_row else "Price"
    normalized_mode = payment_mode.strip().lower()
    show_fine_payment = normalized_mode == "fine"
    show_cash_payment = normalized_mode in {"price", "cash"}
    customer_name = items[0][0] if items else (customer_row[0] if customer_row and customer_row[0] else "")
    mobile = items[0][1] if items else (customer_row[1] if customer_row and customer_row[1] else "")
    city = customer_row[2] if customer_row and customer_row[2] else ""

    return {
        "shop_name": shop_name,
        "owner": owner,
        "shop_contact": shop_contact,
        "shop_address": shop_address,
        "shop_gst_no": shop_gst_no,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "updated_at": updated_at,
        "invoice_date_display": _resolve_invoice_display_datetime(invoice_date, updated_at),
        "updated_at_display": _format_display_datetime(updated_at),
        "customer_name": customer_name,
        "mobile": mobile,
        "city": city,
        "items": items,
        "exchanges": exchanges,
        "items_total": items_total,
        "grand_total": grand_total,
        "total_net_weight": total_net_weight,
        "total_gross_weight": total_gross_weight,
        "amount_paid": float(invoice_row[3] or 0) if invoice_row else 0,
        "remaining_balance": float(invoice_row[4] or 0) if invoice_row else 0,
        "old_balance_included": float(invoice_row[5] or 0) if invoice_row else 0,
        "business_items_fine": float(invoice_row[6] or 0) if invoice_row else 0,
        "business_exchange_fine": float(invoice_row[7] or 0) if invoice_row else 0,
        "from_last_invoice_fine": float(invoice_row[8] or 0) if invoice_row else 0,
        "carry_forward_fine": float(invoice_row[9] or 0) if invoice_row else 0,
        "payment_mode": payment_mode,
        "paid_fine_24k": float(invoice_row[12] or 0) if invoice_row else 0,
        "paid_price_equivalent": float(invoice_row[13] or 0) if invoice_row else 0,
        "paid_fine_display": _weight(invoice_row[12] or 0) if invoice_row and show_fine_payment else "",
        "paid_cash_display": _money(invoice_row[3] or 0) if invoice_row and show_cash_payment else "",
        "remaining_cash_display": _money(invoice_row[4] or 0) if invoice_row and show_cash_payment else "",
        "gst_total": sum(float(row[12] or 0) for row in items),
        "amount_in_words": _amount_in_words(grand_total),
    }

def _write_business_invoice_pdf(payload):
    pdf_dir = os.path.join("Invoices", "BMNPDF")
    os.makedirs(pdf_dir, exist_ok=True)
    pdf_path = os.path.join(pdf_dir, f"invoice_{payload['invoice_number']}.pdf")

    page_width = A4[0]
    margin = 20
    left = margin
    right = page_width - margin
    row_height = 16
    row_count = max(len(payload["items"]), 9)
    body_height = row_count * row_height
    summary_line_gap = 26
    payment_mode = str(payload.get("payment_mode", "Price") or "Price").strip().lower()
    is_cash_mode = payment_mode in {"price", "cash"}
    is_fine_mode = payment_mode == "fine"
    paid_cash_value = payload.get("paid_cash_display") or _money(payload.get("amount_paid", 0))
    paid_fine_value = payload.get("paid_fine_display") or _weight(payload.get("paid_fine_24k", 0))

    summary_rows = [
        ("OLD Invoice Fine (24K):-", _weight(payload["from_last_invoice_fine"])),
        ("OLD Cash Balance:-", _money(payload["old_balance_included"])),
        ("Total Fine (24K):-", _weight(payload["business_items_fine"])),
        (
            "Amount Paid (Cash):-" if is_cash_mode else "Amount Paid (Fine 24K):-",
            paid_cash_value if is_cash_mode else paid_fine_value,
        ),
        (
            "Amount Paid (Fine 24K):-" if is_cash_mode else "Cash Equivalent Paid:-",
            paid_fine_value if is_cash_mode else paid_cash_value,
        ),
        ("Remaining Balance (Cash):-", payload["remaining_cash_display"]),
        ("Carry Forward Fine (24K):-", _weight(payload["carry_forward_fine"])),
    ]
    footer_height = 18 + ((len(summary_rows) - 1) * summary_line_gap) + 18
    content_height = 52 + 62 + 12 + 26 + body_height + 24 + 10 + footer_height
    page_height = max(A4[1], content_height + (margin * 2))

    c = canvas.Canvas(pdf_path, pagesize=(page_width, page_height))
    top = page_height - margin
    bottom = margin

    c.setLineWidth(1.8)
    c.rect(left, bottom, right - left, top - bottom)

    header_bottom = top - 52
    customer_bottom = header_bottom - 62
    table_top = customer_bottom - 12
    table_header_bottom = table_top - 26
    table_bottom = table_header_bottom - body_height
    totals_bottom = table_bottom - 24
    lower_top = totals_bottom - 10

    for y in [header_bottom, customer_bottom, table_top, table_header_bottom, table_bottom, totals_bottom]:
        c.line(left, y, right, y)

    _draw_text(c, left + 12, top - 16, f"Owner Name: {_safe_text(payload['owner'])}", "Helvetica-Bold", 9, max_width=185)
    _draw_text(c, left + 12, top - 38, f"Address:- {_safe_text(payload['shop_address'])}", "Helvetica-Bold", 8.5, max_width=235)
    _draw_text(c, right - 150, top - 16, f"Contact No:- {_safe_text(payload['shop_contact'])}", "Helvetica-Bold", 9, max_width=138)
    _draw_text(c, right - 170, top - 38, f"GSTIN:- {_safe_text(payload['shop_gst_no'])}", "Helvetica-Bold", 9, max_width=158)

    _draw_text(c, (left + right) / 2, top - 19, f"\"{payload['shop_name'].upper()}\"", "Times-Bold", 17, "center", 230)
    _draw_text(c, (left + right) / 2, top - 41, "||SHREE||", "Times-Bold", 11, "center")

    _draw_text(c, left + 18, header_bottom - 18, f"Name: {_safe_text(payload['customer_name'])}", "Helvetica-Bold", 9)
    _draw_text(c, left + 18, header_bottom - 38, f"City:- {_safe_text(payload['city'])}", "Helvetica-Bold", 9, max_width=250)
    _draw_text(c, left + 18, header_bottom - 58, f"Mobile Number:- {_safe_text(payload['mobile'])}", "Helvetica-Bold", 9)
    _draw_text(c, right - 170, header_bottom - 14, f"Invoice Number:- {payload['invoice_number']}", "Helvetica-Bold", 9)
    _draw_text(c, right - 190, header_bottom - 36, f"Date:- {payload['invoice_date_display']}", "Helvetica-Bold", 9, max_width=175)

    column_specs = [
        ("Product", 0.18),
        ("Net WT.", 0.09),
        ("Gross WT.", 0.09),
        ("Purity", 0.09),
        ("Wastage %", 0.11),
        ("Rate", 0.07),
        ("Fine", 0.07),
        ("Amount", 0.10),
        ("GST Amount", 0.09),
        ("Final Price", 0.11),
    ]
    table_width = right - left - 16
    column_x = [left + 8]
    running = left + 8
    for _, ratio in column_specs:
        running += table_width * ratio
        column_x.append(running)

    for x in column_x:
        c.line(x, table_top, x, totals_bottom)

    header_y = table_top - 17
    for index, (label, _) in enumerate(column_specs):
        x1 = column_x[index]
        x2 = column_x[index + 1]
        _draw_text(c, (x1 + x2) / 2, header_y, label, "Helvetica-Bold", 9, "center", x2 - x1 - 6)

    body_top = table_header_bottom - 14
    for index in range(row_count):
        y = body_top - index * row_height
        if index >= len(payload["items"]):
            continue
        item = payload["items"][index]
        values = [
            item[2],
            _weight(item[3]),
            _weight(item[4]),
            _money(item[5]),
            _money(item[6]),
            _money(item[8]),
            _weight(item[9]),
            _money(item[10]),
            _money(item[12]),
            _money(item[13]),
        ]
        for col_index, value in enumerate(values):
            x1 = column_x[col_index]
            x2 = column_x[col_index + 1]
            align = "left" if col_index == 0 else "right"
            x = x1 + 4 if align == "left" else x2 - 4
            _draw_text(c, x, y, value, "Helvetica", 8.5, align, x2 - x1 - 8)

    totals_y = table_bottom - 16
    totals = [
        (0, 1, "Totals:-", "left"),
        (1, 2, _weight(payload["total_net_weight"]), "center"),
        (2, 3, _weight(payload["total_gross_weight"]), "center"),
        (6, 7, _weight(payload["business_items_fine"]), "center"),
        (7, 8, _money(payload["items_total"]), "center"),
        (8, 9, _money(payload["gst_total"]), "center"),
        (9, 10, _money(payload["grand_total"]), "center"),
    ]
    for start, end, text, align in totals:
        x1 = column_x[start]
        x2 = column_x[end]
        x = x1 + 4 if align == "left" else (x1 + x2) / 2
        _draw_text(c, x, totals_y, text, "Helvetica-Bold", 8.5, align, x2 - x1 - 8)

    lower_left = left + 8
    lower_right = right - 8
    lower_mid = lower_left + (lower_right - lower_left) * 0.62
    lower_inner_right = lower_mid + (lower_right - lower_mid) * 0.72
    footer_bottom = lower_top - 18 - ((len(summary_rows) - 1) * summary_line_gap) - 18

    c.rect(lower_left, footer_bottom, lower_right - lower_left, lower_top - footer_bottom)
    c.line(lower_mid, footer_bottom, lower_mid, lower_top)
    c.line(lower_inner_right, footer_bottom, lower_inner_right, lower_top)

    amount_letters_bottom = lower_top - 52
    c.line(lower_left, amount_letters_bottom, lower_mid, amount_letters_bottom)
    _draw_text(c, lower_left + 8, lower_top - 18, "Amount in letters:-", "Helvetica-Bold", 9)
    wrapped_amount = _wrap_text_lines(
        payload["amount_in_words"],
        "Helvetica",
        8.5,
        lower_mid - lower_left - 128,
        max_lines=2,
    )
    for index, line in enumerate(wrapped_amount):
        _draw_text(c, lower_left + 120, lower_top - 18 - (index * 14), line, "Helvetica", 8.5)

    _draw_text(c, lower_left + 8, amount_letters_bottom - 22, "Narration:-", "Helvetica-Bold", 9)
    _draw_text(c, (lower_left + lower_mid) / 2, footer_bottom + 10, '"Thanks Do Visit Again"', "Times-Bold", 10, "center")
    summary_y = lower_top - 18
    for label, value in summary_rows:
        _draw_text(
            c,
            lower_mid + 8,
            summary_y,
            label,
            "Helvetica-Bold",
            8.5,
            max_width=lower_inner_right - lower_mid - 16,
        )
        _draw_text(c, lower_right - 8, summary_y, value, "Helvetica", 8.5, "right")
        summary_y -= summary_line_gap

    c.save()
    return pdf_path


def print_business_invoice_for(conn, invoice_number, send_to_printer=True):
    try:
        payload = _fetch_business_invoice_payload(conn, invoice_number)
        return _write_business_invoice_pdf(payload)
    except Exception as exc:
        logging.error(f"Error in print_business_invoice_for: {exc}")
        raise


def refresh_customer_invoice_files(conn, customer_code):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT invoice_number, COALESCE(invoice_type, 'retail')
        FROM invoices
        WHERE customer_code = ?
        ORDER BY id
        """,
        (customer_code,),
    )
    for invoice_number, invoice_type in cursor.fetchall():
        if invoice_type == "business":
            print_business_invoice_for(conn, invoice_number, send_to_printer=False)
        else:
            print_invoice_for(conn, invoice_number, send_to_printer=False)
