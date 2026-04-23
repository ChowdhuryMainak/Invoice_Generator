import tkinter as tk
from tkinter import messagebox, filedialog
from datetime import datetime
import sqlite3
import os
import csv
import shutil
from tkinter import ttk
from invoice_utils import (
    print_invoice_for,
    print_business_invoice_for,
    refresh_customer_invoice_files,
    _fetch_retail_invoice_payload,
    _fetch_business_invoice_payload,
)
import pandas as pd
import logging

APP_NAME = "Gold Shop Invoice System"
APP_VERSION = "1.1.1"
ADMIN_SUPPORT_CONTACT = {
    "Department": "Tech Department",
    "Support Type": "Admin Software Support",
    "Contact Number": "+91-7385656714",
    "Email": "mainakfaith005@gmail.com",
    "Address": "Tech Department, Gold Shop Head Office",
    "Working Hours": "10:00 AM - 7:00 PM",
}


def _normalize_customer_role(value):
    role = str(value or "").strip().lower()
    return "BusinessMan" if role == "businessman" else "Customer"


def _display_customer_role(value):
    return "Businessman" if _normalize_customer_role(value) == "BusinessMan" else "Customer"


def _normalize_report_role_filter(value):
    role = str(value or "").strip().lower().replace(" ", "")
    if role in {"", "all", "allroles"}:
        return None
    if role == "businessman":
        return "BusinessMan"
    return "Customer"


def _set_hidden_windows(path):
    if os.name != "nt":
        return
    if not path or not os.path.exists(path):
        return
    try:
        import ctypes
        file_attribute_hidden = 0x02
        existing_attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if existing_attrs == -1:
            return
        ctypes.windll.kernel32.SetFileAttributesW(str(path), existing_attrs | file_attribute_hidden)
    except Exception:
        pass


def _resolve_app_db_path():
    base_dir = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    app_dir = os.path.join(base_dir, "JeweleryInvoiceSystem", "data")
    os.makedirs(app_dir, exist_ok=True)
    db_path = os.path.join(app_dir, "customers.db")

    legacy_db_path = os.path.join(os.getcwd(), "customers.db")
    if not os.path.exists(db_path) and os.path.exists(legacy_db_path):
        try:
            shutil.move(legacy_db_path, db_path)
        except Exception:
            try:
                shutil.copy2(legacy_db_path, db_path)
            except Exception:
                pass

    _set_hidden_windows(app_dir)
    _set_hidden_windows(db_path)
    return db_path


def _connect_app_db(timeout=30):
    db_path = _resolve_app_db_path()
    conn = sqlite3.connect(db_path, timeout=timeout)
    _set_hidden_windows(os.path.dirname(db_path))
    _set_hidden_windows(db_path)
    return conn


def _next_invoice_number(conn, now=None):
    current_date = now or datetime.now()
    prefix = f"{current_date.year}{current_date.month:02d}"
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(MAX(CAST(SUBSTR(invoice_number, -3) AS INTEGER)), 0)
        FROM invoices
        WHERE invoice_number LIKE ?
        """,
        (f"{prefix}%",),
    )
    next_sequence = int(cursor.fetchone()[0] or 0) + 1
    return f"{prefix}{next_sequence:03d}"


def _format_history_datetime(value):
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%d-%m-%Y %I:%M %p") if fmt.endswith("%S") else parsed.strftime("%d-%m-%Y")
        except ValueError:
            continue
    return text


def _create_scrollable_page(root):
    container = tk.Frame(root)
    container.pack(fill=tk.BOTH, expand=True)

    canvas = tk.Canvas(container, highlightthickness=0)
    scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)

    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    content = tk.Frame(canvas)
    window_id = canvas.create_window((0, 0), window=content, anchor="nw")

    def _sync_scrollregion(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _resize_content(event):
        canvas.itemconfigure(window_id, width=event.width)
        canvas.configure(scrollregion=canvas.bbox("all"))

    content.bind("<Configure>", _sync_scrollregion)
    canvas.bind("<Configure>", _resize_content)
    return container, canvas, content


def _bind_mousewheel_to_canvas(widget, canvas):
    if getattr(widget, "_mousewheel_bound", False):
        return

    def _on_mousewheel(event):
        if event.delta:
            delta = -int(event.delta / 120)
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1
        canvas.yview_scroll(delta, "units")
        return "break"

    widget.bind("<MouseWheel>", _on_mousewheel, add="+")
    widget.bind("<Button-4>", _on_mousewheel, add="+")
    widget.bind("<Button-5>", _on_mousewheel, add="+")
    widget._mousewheel_bound = True


def _iter_focusable_widgets(container):
    focusable_classes = {
        "Entry", "TEntry",
        "Button", "TButton",
        "Checkbutton", "TCheckbutton",
        "Combobox", "TCombobox",
        "Radiobutton", "TRadiobutton",
    }
    widgets = []

    def _walk(widget):
        try:
            if widget.winfo_class() in focusable_classes:
                state = str(widget.cget("state")) if "state" in widget.keys() else "normal"
                if state != "disabled":
                    widgets.append(widget)
        except Exception:
            pass

        for child in widget.winfo_children():
            _walk(child)

    _walk(container)
    widgets.sort(key=lambda item: (item.winfo_rooty(), item.winfo_rootx()))
    return widgets


def _iter_all_widgets(container):
    widgets = [container]
    for child in container.winfo_children():
        widgets.extend(_iter_all_widgets(child))
    return widgets


def _focus_widget(widget):
    try:
        widget.focus_set()
        if widget.winfo_class() in {"Entry", "TEntry"}:
            widget.icursor(tk.END)
    except Exception:
        pass


def _ensure_widget_visible(canvas, content, widget):
    try:
        canvas.update_idletasks()
        content_height = max(content.winfo_height(), 1)
        widget_top = widget.winfo_rooty() - content.winfo_rooty()
        widget_bottom = widget_top + widget.winfo_height()
        view_top = canvas.canvasy(0)
        view_bottom = view_top + canvas.winfo_height()

        if widget_top < view_top:
            canvas.yview_moveto(max(0, widget_top / content_height))
        elif widget_bottom > view_bottom:
            new_top = max(0, widget_bottom - canvas.winfo_height())
            canvas.yview_moveto(min(1, new_top / content_height))
    except Exception:
        pass


def _move_focus(container, current_widget, direction):
    widgets = _iter_focusable_widgets(container)
    if not widgets:
        return None

    if current_widget not in widgets:
        return widgets[0]

    if direction == "next":
        current_index = widgets.index(current_widget)
        return widgets[(current_index + 1) % len(widgets)]

    current_x = current_widget.winfo_rootx()
    current_y = current_widget.winfo_rooty()
    candidates = []
    for widget in widgets:
        if widget == current_widget:
            continue
        widget_x = widget.winfo_rootx()
        widget_y = widget.winfo_rooty()
        dx = widget_x - current_x
        dy = widget_y - current_y

        if direction == "right" and dx > 0:
            score = (dx * 2) + abs(dy)
        elif direction == "left" and dx < 0:
            score = (abs(dx) * 2) + abs(dy)
        elif direction == "down" and dy > 0:
            score = (dy * 2) + abs(dx)
        elif direction == "up" and dy < 0:
            score = (abs(dy) * 2) + abs(dx)
        else:
            continue
        candidates.append((score, widget))

    if candidates:
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    current_index = widgets.index(current_widget)
    if direction in {"right", "down"}:
        return widgets[(current_index + 1) % len(widgets)]
    return widgets[(current_index - 1) % len(widgets)]


def _bind_keyboard_navigation(widget, container, canvas=None, content=None):
    if getattr(widget, "_keyboard_nav_bound", False):
        return

    def _navigate(direction):
        next_widget = _move_focus(container, widget, direction)
        if next_widget:
            _focus_widget(next_widget)
            if canvas and content:
                _ensure_widget_visible(canvas, content, next_widget)
        return "break"

    widget.bind("<Return>", lambda _event: _navigate("next"), add="+")
    widget.bind("<Down>", lambda _event: _navigate("down"), add="+")
    widget.bind("<Up>", lambda _event: _navigate("up"), add="+")
    widget.bind("<Left>", lambda _event: _navigate("left"), add="+")
    widget.bind("<Right>", lambda _event: _navigate("right"), add="+")
    widget._keyboard_nav_bound = True

class CSVUtils:

    @staticmethod
    def export_master_csv(conn):
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(invoices)")
            invoice_columns = {row[1] for row in cursor.fetchall()}
            updated_at_select = "i.updated_at" if "updated_at" in invoice_columns else "i.invoice_date"
            invoice_type_select = "COALESCE(i.invoice_type, 'retail')" if "invoice_type" in invoice_columns else "'retail'"
            customer_role_select = "COALESCE(i.customer_role, 'Customer')" if "customer_role" in invoice_columns else "'Customer'"
            payment_mode_select = "COALESCE(i.payment_mode, 'Price')" if "payment_mode" in invoice_columns else "'Price'"
            paid_fine_select = "COALESCE(i.paid_fine_24k, 0)" if "paid_fine_24k" in invoice_columns else "0"
            paid_price_equivalent_select = "COALESCE(i.paid_price_equivalent, 0)" if "paid_price_equivalent" in invoice_columns else "0"
            business_items_fine_select = "COALESCE(i.business_items_fine, 0)" if "business_items_fine" in invoice_columns else "0"
            business_exchange_fine_select = "COALESCE(i.business_exchange_fine, 0)" if "business_exchange_fine" in invoice_columns else "0"
            from_last_invoice_fine_select = "COALESCE(i.from_last_invoice_fine, 0)" if "from_last_invoice_fine" in invoice_columns else "0"
            carry_forward_fine_select = "COALESCE(i.carry_forward_fine, 0)" if "carry_forward_fine" in invoice_columns else "0"

            cursor.execute("""
                SELECT i.customer_code,
                       COALESCE(c.name, '') AS customer_name,
                       COALESCE(c.mobile, '') AS mobile,
                       i.invoice_number,
                       i.invoice_date,
                       {updated_at_select},
                       {invoice_type_select},
                       {customer_role_select},
                       {payment_mode_select},
                       {paid_fine_select},
                       {paid_price_equivalent_select},
                       COALESCE(i.items_total, 0),
                       COALESCE(i.exchange_total, 0),
                       COALESCE(i.old_balance_included, 0),
                       COALESCE(i.grand_total, 0),
                       COALESCE(i.amount_paid, 0),
                       COALESCE(i.remaining_balance, 0),
                       {business_items_fine_select},
                       {business_exchange_fine_select},
                       {from_last_invoice_fine_select},
                       {carry_forward_fine_select}
                FROM invoices i
                LEFT JOIN customers c ON c.customer_code = i.customer_code
                ORDER BY i.invoice_number
            """.format(
                updated_at_select=updated_at_select,
                invoice_type_select=invoice_type_select,
                customer_role_select=customer_role_select,
                payment_mode_select=payment_mode_select,
                paid_fine_select=paid_fine_select,
                paid_price_equivalent_select=paid_price_equivalent_select,
                business_items_fine_select=business_items_fine_select,
                business_exchange_fine_select=business_exchange_fine_select,
                from_last_invoice_fine_select=from_last_invoice_fine_select,
                carry_forward_fine_select=carry_forward_fine_select,
            ))

            invoice_rows = cursor.fetchall()

            if not invoice_rows:
                return

            invoice_map = {
                row[3]: {
                    "customer_code": row[0],
                    "customer_name": row[1],
                    "mobile": row[2],
                    "invoice_number": row[3],
                    "invoice_date": row[4],
                    "updated_at": row[5],
                    "invoice_type": row[6],
                    "customer_role": row[7],
                    "payment_mode": row[8],
                    "paid_fine_24k": row[9],
                    "paid_price_equivalent": row[10],
                    "items_total": row[11],
                    "exchange_total": row[12],
                    "old_balance_included": row[13],
                    "grand_total": row[14],
                    "amount_paid": row[15],
                    "remaining_balance": row[16],
                    "business_items_fine": row[17],
                    "business_exchange_fine": row[18],
                    "from_last_invoice_fine": row[19],
                    "carry_forward_fine": row[20],
                }
                for row in invoice_rows
            }

            cursor.execute("""
                SELECT customer_code, customer_name, mobile, invoice_number,
                       product_name, category, quantity,
                       net_weight, gross_weight,
                       rate_per_gram, making_charges_per_gram,
                       total_making_charges, total_rupees,
                       gst_percent, gst_amount, final_total
                FROM invoice_details
                ORDER BY invoice_number, id
            """)
            sale_rows = cursor.fetchall()

            cursor.execute("""
                SELECT customer_code, invoice_number, invoice_date,
                       item_description, net_weight, purity_percent,
                       rate_per_gram, exchange_amount
                FROM exchange_details
                ORDER BY invoice_number, id
            """)
            exchange_rows = cursor.fetchall()

            cursor.execute("""
                SELECT customer_code, customer_name, mobile, invoice_number,
                       product_name, net_weight, gross_weight, purity,
                       wastage_percent, labour, rate_per_gram, fine_24k,
                       total_rate, gst_percent, gst_amount, final_price
                FROM business_invoice_details
                ORDER BY invoice_number, id
            """)
            business_sale_rows = cursor.fetchall()

            cursor.execute("""
                SELECT customer_code, invoice_number, invoice_date,
                       product_name, net_weight, purity, fine_24k
                FROM business_exchange_details
                ORDER BY invoice_number, id
            """)
            business_exchange_rows = cursor.fetchall()

            sales_by_invoice = {}
            for row in sale_rows:
                sales_by_invoice.setdefault(row[3], []).append(row)

            exchanges_by_invoice = {}
            for row in exchange_rows:
                exchanges_by_invoice.setdefault(row[1], []).append(row)

            business_sales_by_invoice = {}
            for row in business_sale_rows:
                business_sales_by_invoice.setdefault(row[3], []).append(row)

            business_exchanges_by_invoice = {}
            for row in business_exchange_rows:
                business_exchanges_by_invoice.setdefault(row[1], []).append(row)

            os.makedirs("Invoices", exist_ok=True)
            csv_file_path = os.path.join("Invoices", "master_invoice.csv")

            # ðŸ”¥ FILE LOCK CHECK
            if os.path.exists(csv_file_path):
                try:
                    os.rename(csv_file_path, csv_file_path)
                except PermissionError:
                    messagebox.showwarning(
                        "File Open",
                        "master_invoice.csv is currently open.\n"
                        "Please close the file and try again."
                    )
                    return

            with open(csv_file_path, mode="w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)

                writer.writerow([
                    "Customer Code", "Customer Name", "Mobile", "Invoice Number", "Invoice Date", "Updated At",
                    "Customer Role", "Invoice Type", "Row Type",
                    "Product Name", "Category", "Quantity",
                    "Net Weight (per qty)", "Gross Weight (per qty)",
                    "Total Net Weight", "Total Gross Weight",
                    "Rate Per Gram", "Making Charges Per Gram",
                    "Total Making Charges", "Base Total",
                    "GST %", "GST Amount",
                    "Sale Line Total",
                    "Exchange Item Description", "Exchange Net Weight",
                    "Exchange Purity %", "Exchange Rate Per Gram", "Exchange Amount",
                    "Invoice Items Total", "Invoice Exchange Total",
                    "Old Balance Included", "Grand Total",
                    "Amount Paid", "Remaining Balance", "Payment Mode", "Paid Fine (24K)", "Paid Price Equivalent",
                    "Business Net Weight", "Business Gross Weight", "Business Purity",
                    "Business WSTG %", "Business Fine (24K)", "Business Rate Per Gram",
                    "Business Labour", "Business Amount Before GST", "Business GST %",
                    "Business GST Amount",
                    "Business Final Price", "Business Exchange Product", "Business Exchange Fine (24K)",
                    "From Last Invoice Fine", "Carry Forward Fine"
                ])

                for invoice_number, invoice_data in invoice_map.items():
                    if invoice_data["invoice_type"] == "business":
                        emitted_invoice_totals = False
                        for row in business_sales_by_invoice.get(invoice_number, []):
                            if not emitted_invoice_totals:
                                emitted_invoice_totals = True
                                business_items_fine = invoice_data["business_items_fine"]
                                business_exchange_fine = invoice_data["business_exchange_fine"]
                                from_last_invoice_fine = invoice_data["from_last_invoice_fine"]
                                carry_forward_fine = invoice_data["carry_forward_fine"]
                                amount_paid = invoice_data["amount_paid"]
                                remaining_balance = invoice_data["remaining_balance"]
                                grand_total = invoice_data["grand_total"]
                                old_balance_included = invoice_data["old_balance_included"]
                                payment_mode = invoice_data["payment_mode"]
                                paid_fine_24k = invoice_data["paid_fine_24k"]
                                paid_price_equivalent = invoice_data["paid_price_equivalent"]
                            else:
                                business_items_fine = ""
                                business_exchange_fine = ""
                                from_last_invoice_fine = ""
                                carry_forward_fine = ""
                                amount_paid = ""
                                remaining_balance = ""
                                grand_total = ""
                                old_balance_included = ""
                                payment_mode = ""
                                paid_fine_24k = ""
                                paid_price_equivalent = ""

                            writer.writerow([
                                invoice_data["customer_code"], invoice_data["customer_name"], invoice_data["mobile"],
                                invoice_number, invoice_data["invoice_date"], invoice_data["updated_at"],
                                invoice_data["customer_role"], invoice_data["invoice_type"], "Business Sale",
                                row[4], "", "",
                                "", "", "", "",
                                "", "", "", "",
                                "", "", "", "", "",
                                invoice_data["items_total"], "",
                                old_balance_included, grand_total,
                                amount_paid, remaining_balance, payment_mode, paid_fine_24k, paid_price_equivalent,
                                row[5], row[6], row[7], row[8], row[11], row[10],
                                row[9], row[12], row[13], row[14],
                                row[15], "", "",
                                from_last_invoice_fine, carry_forward_fine
                            ])

                        for row in business_exchanges_by_invoice.get(invoice_number, []):
                            if not emitted_invoice_totals:
                                emitted_invoice_totals = True
                                business_items_fine = invoice_data["business_items_fine"]
                                business_exchange_fine = invoice_data["business_exchange_fine"]
                                from_last_invoice_fine = invoice_data["from_last_invoice_fine"]
                                carry_forward_fine = invoice_data["carry_forward_fine"]
                                amount_paid = invoice_data["amount_paid"]
                                remaining_balance = invoice_data["remaining_balance"]
                                grand_total = invoice_data["grand_total"]
                                old_balance_included = invoice_data["old_balance_included"]
                                payment_mode = invoice_data["payment_mode"]
                                paid_fine_24k = invoice_data["paid_fine_24k"]
                                paid_price_equivalent = invoice_data["paid_price_equivalent"]
                            else:
                                business_items_fine = ""
                                business_exchange_fine = ""
                                from_last_invoice_fine = ""
                                carry_forward_fine = ""
                                amount_paid = ""
                                remaining_balance = ""
                                grand_total = ""
                                old_balance_included = ""
                                payment_mode = ""
                                paid_fine_24k = ""
                                paid_price_equivalent = ""

                            writer.writerow([
                                invoice_data["customer_code"], invoice_data["customer_name"], invoice_data["mobile"],
                                invoice_number, invoice_data["invoice_date"], invoice_data["updated_at"],
                                invoice_data["customer_role"], invoice_data["invoice_type"], "Business Exchange",
                                "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                                invoice_data["items_total"], "",
                                old_balance_included, grand_total,
                                amount_paid, remaining_balance, payment_mode, paid_fine_24k, paid_price_equivalent,
                                "", "", "", "", "", "", "", "", "", "",
                                row[3], row[6],
                                from_last_invoice_fine, carry_forward_fine
                            ])
                        continue

                    invoice_sales = sales_by_invoice.get(invoice_number, [])
                    invoice_exchanges = exchanges_by_invoice.get(invoice_number, [])
                    emitted_invoice_totals = False

                    for row in invoice_sales:
                        quantity = float(row[6])
                        net_weight = float(row[7])
                        gross_weight = float(row[8])

                        if not emitted_invoice_totals:
                            items_total = invoice_data["items_total"]
                            exchange_total = invoice_data["exchange_total"]
                            old_balance_included = invoice_data["old_balance_included"]
                            grand_total = invoice_data["grand_total"]
                            amount_paid = invoice_data["amount_paid"]
                            remaining_balance = invoice_data["remaining_balance"]
                            payment_mode = invoice_data["payment_mode"]
                            paid_fine_24k = invoice_data["paid_fine_24k"]
                            paid_price_equivalent = invoice_data["paid_price_equivalent"]
                            emitted_invoice_totals = True
                        else:
                            items_total = ""
                            exchange_total = ""
                            old_balance_included = ""
                            grand_total = ""
                            amount_paid = ""
                            remaining_balance = ""
                            payment_mode = ""
                            paid_fine_24k = ""
                            paid_price_equivalent = ""

                        writer.writerow([
                            row[0], row[1], row[2], row[3], invoice_data["invoice_date"], invoice_data["updated_at"],
                            invoice_data["customer_role"], invoice_data["invoice_type"], "Sale",
                            row[4], row[5], quantity,
                            net_weight, gross_weight,
                            quantity * net_weight,
                            quantity * gross_weight,
                            row[9], row[10], row[11], row[12],
                            row[13], row[14], row[15],
                            "", "", "", "", "",
                            items_total, exchange_total,
                            old_balance_included, grand_total,
                            amount_paid, remaining_balance, payment_mode, paid_fine_24k, paid_price_equivalent,
                            "", "", "", "", "", "", "", "", "", "", "", "",
                            "", ""
                        ])

                    for row in invoice_exchanges:
                        if not emitted_invoice_totals:
                            items_total = invoice_data["items_total"]
                            exchange_total = invoice_data["exchange_total"]
                            old_balance_included = invoice_data["old_balance_included"]
                            grand_total = invoice_data["grand_total"]
                            amount_paid = invoice_data["amount_paid"]
                            remaining_balance = invoice_data["remaining_balance"]
                            payment_mode = invoice_data["payment_mode"]
                            paid_fine_24k = invoice_data["paid_fine_24k"]
                            paid_price_equivalent = invoice_data["paid_price_equivalent"]
                            emitted_invoice_totals = True
                        else:
                            items_total = ""
                            exchange_total = ""
                            old_balance_included = ""
                            grand_total = ""
                            amount_paid = ""
                            remaining_balance = ""
                            payment_mode = ""
                            paid_fine_24k = ""
                            paid_price_equivalent = ""

                        writer.writerow([
                            invoice_data["customer_code"],
                            invoice_data["customer_name"],
                            invoice_data["mobile"],
                            invoice_number,
                            invoice_data["invoice_date"],
                            invoice_data["updated_at"],
                            invoice_data["customer_role"],
                            invoice_data["invoice_type"],
                            "Exchange",
                            "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                            row[3], row[4], row[5], row[6], row[7],
                            items_total, exchange_total,
                            old_balance_included, grand_total,
                            amount_paid, remaining_balance, payment_mode, paid_fine_24k, paid_price_equivalent,
                            "", "", "", "", "", "", "", "", "", "", "", "",
                            "", ""
                        ])

        except Exception as e:
            logging.error(f"CSV export error: {e}")       

class InvoiceApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.state('zoomed')
        try:
            self.create_database()
            self.create_pin_entry_page()
        except Exception as e:
            logging.error(f"Error during initialization: {e}")
            messagebox.showerror("Error", f"Failed to initialize application: {e}")
            root.destroy()
            
    def create_database(self):
        try:
            self.conn = _connect_app_db(timeout=30)
            self.cursor = self.conn.cursor()
            self.cursor.execute("PRAGMA busy_timeout = 30000")

            # ================= CUSTOMERS =================
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS customers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    city TEXT NOT NULL,
                    mobile TEXT NOT NULL UNIQUE,
                    customer_code TEXT NOT NULL,
                    role TEXT DEFAULT 'Customer',
                    balance REAL DEFAULT 0,
                    fine_balance REAL DEFAULT 0,
                    is_active INTEGER DEFAULT 1
                )
            """)

            for column_def in [
                "role TEXT DEFAULT 'Customer'",
                "fine_balance REAL DEFAULT 0",
            ]:
                try:
                    self.cursor.execute(
                        f"ALTER TABLE customers ADD COLUMN {column_def}"
                    )
                except:
                    pass

            # ================= INVOICES =================
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_code TEXT NOT NULL,
                    invoice_number TEXT NOT NULL UNIQUE,
                    invoice_date TEXT,
                    updated_at TEXT,
                    invoice_type TEXT DEFAULT 'retail',
                    customer_role TEXT DEFAULT 'Customer',
                    items_total REAL DEFAULT 0,
                    exchange_total REAL DEFAULT 0,
                    old_balance_included REAL DEFAULT 0,
                    grand_total REAL DEFAULT 0,
                    amount_paid REAL DEFAULT 0,
                    remaining_balance REAL DEFAULT 0,
                    payment_mode TEXT DEFAULT 'Price',
                    paid_fine_24k REAL DEFAULT 0,
                    paid_price_equivalent REAL DEFAULT 0,
                    business_items_fine REAL DEFAULT 0,
                    business_exchange_fine REAL DEFAULT 0,
                    from_last_invoice_fine REAL DEFAULT 0,
                    carry_forward_fine REAL DEFAULT 0,
                    FOREIGN KEY (customer_code)
                        REFERENCES customers (customer_code)
                )
            """)

            # ================= INVOICE DETAILS =================
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS invoice_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_code TEXT NOT NULL,
                    invoice_number TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    mobile TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    net_weight REAL NOT NULL,
                    gross_weight REAL NOT NULL,
                    rate_per_gram REAL NOT NULL,
                    making_charges_per_gram REAL NOT NULL,
                    total_making_charges REAL NOT NULL,
                    total_rupees REAL NOT NULL,
                    gst_percent REAL DEFAULT 0,
                    gst_amount REAL DEFAULT 0,
                    final_total REAL DEFAULT 0,
                    amount_paid REAL NOT NULL,
                    remaining_balance REAL NOT NULL,
                    category TEXT NOT NULL,
                    FOREIGN KEY (customer_code)
                        REFERENCES customers (customer_code)
                )
            """)

            # -------- SAFE UPGRADE: ADD INVOICE DATE COLUMN --------
            try:
                self.cursor.execute(
                    "ALTER TABLE invoice_details ADD COLUMN invoice_date TEXT"
                )
            except:
                pass

            for column_def in [
                "invoice_date TEXT",
                "updated_at TEXT",
                "invoice_type TEXT DEFAULT 'retail'",
                "customer_role TEXT DEFAULT 'Customer'",
                "items_total REAL DEFAULT 0",
                "exchange_total REAL DEFAULT 0",
                "old_balance_included REAL DEFAULT 0",
                "grand_total REAL DEFAULT 0",
                "amount_paid REAL DEFAULT 0",
                "remaining_balance REAL DEFAULT 0",
                "payment_mode TEXT DEFAULT 'Price'",
                "paid_fine_24k REAL DEFAULT 0",
                "paid_price_equivalent REAL DEFAULT 0",
                "business_items_fine REAL DEFAULT 0",
                "business_exchange_fine REAL DEFAULT 0",
                "from_last_invoice_fine REAL DEFAULT 0",
                "carry_forward_fine REAL DEFAULT 0",
            ]:
                try:
                    self.cursor.execute(
                        f"ALTER TABLE invoices ADD COLUMN {column_def}"
                    )
                except:
                    pass

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS exchange_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_code TEXT NOT NULL,
                    invoice_number TEXT NOT NULL,
                    invoice_date TEXT,
                    item_description TEXT NOT NULL,
                    net_weight REAL NOT NULL,
                    purity_percent REAL NOT NULL,
                    rate_per_gram REAL NOT NULL,
                    exchange_amount REAL NOT NULL,
                    FOREIGN KEY (customer_code)
                        REFERENCES customers (customer_code)
                )
            """)

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS business_invoice_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_code TEXT NOT NULL,
                    invoice_number TEXT NOT NULL,
                    invoice_date TEXT,
                    customer_name TEXT NOT NULL,
                    mobile TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    net_weight REAL NOT NULL,
                    gross_weight REAL NOT NULL,
                    purity REAL DEFAULT 0,
                    wastage_percent REAL DEFAULT 0,
                    labour REAL DEFAULT 0,
                    rate_per_gram REAL DEFAULT 0,
                    fine_24k REAL DEFAULT 0,
                    total_rate REAL DEFAULT 0,
                    gst_percent REAL DEFAULT 0,
                    gst_amount REAL DEFAULT 0,
                    final_price REAL DEFAULT 0,
                    FOREIGN KEY (customer_code)
                        REFERENCES customers (customer_code)
                )
            """)

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS business_exchange_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_code TEXT NOT NULL,
                    invoice_number TEXT NOT NULL,
                    invoice_date TEXT,
                    product_name TEXT NOT NULL,
                    net_weight REAL NOT NULL,
                    purity REAL DEFAULT 0,
                    fine_24k REAL DEFAULT 0,
                    FOREIGN KEY (customer_code)
                        REFERENCES customers (customer_code)
                )
            """)

            # ================= STOCK =================
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_name TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    net_weight REAL NOT NULL,
                    gross_weight REAL NOT NULL
                )
            """)

            # ================= SETTINGS =================
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shop_name TEXT,
                    owner_name TEXT,
                    shop_contact TEXT,
                    shop_address TEXT,
                    shop_gst_no TEXT,
                    making_charges_22c REAL DEFAULT 10.0,
                    making_charges_24c REAL DEFAULT 100.0,
                    gst_percent REAL DEFAULT 3.0,
                    gst_enabled INTEGER DEFAULT 1
                )
            """)

            for column_def in [
                "shop_contact TEXT",
                "shop_address TEXT",
                "shop_gst_no TEXT",
            ]:
                try:
                    self.cursor.execute(
                        f"ALTER TABLE settings ADD COLUMN {column_def}"
                    )
                except:
                    pass

            # ================= PINS =================
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS pins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pin_type TEXT NOT NULL UNIQUE,
                    pin_value TEXT NOT NULL
                )
            """)

            self.conn.commit()

            self.cursor.execute("""
                UPDATE invoices
                SET updated_at = COALESCE(updated_at, invoice_date)
                WHERE updated_at IS NULL OR updated_at = ''
            """)
            self.cursor.execute("""
                UPDATE invoices
                SET invoice_type = COALESCE(NULLIF(invoice_type, ''), 'retail'),
                    customer_role = COALESCE(NULLIF(customer_role, ''), 'Customer'),
                    payment_mode = COALESCE(NULLIF(payment_mode, ''), 'Price'),
                    paid_fine_24k = COALESCE(paid_fine_24k, 0),
                    paid_price_equivalent = COALESCE(paid_price_equivalent, 0)
            """)
            self.cursor.execute("""
                UPDATE customers
                SET role = COALESCE(NULLIF(role, ''), 'Customer'),
                    fine_balance = COALESCE(fine_balance, 0)
            """)

            # ================= DEFAULT SETTINGS INSERT =================
            self.cursor.execute("SELECT COUNT(*) FROM settings")
            if self.cursor.fetchone()[0] == 0:
                self.cursor.execute("""
                    INSERT INTO settings (
                        shop_name,
                        owner_name,
                        shop_contact,
                        shop_address,
                        shop_gst_no,
                        making_charges_22c,
                        making_charges_24c,
                        gst_percent,
                        gst_enabled
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, ("Gold Shop", "Owner", "", "", "", 10.0, 100.0, 3.0, 1))

            # ================= DEFAULT PINS INSERT =================
            self.cursor.execute("SELECT COUNT(*) FROM pins WHERE pin_type='software'")
            if self.cursor.fetchone()[0] == 0:
                self.cursor.execute("""
                    INSERT INTO pins (pin_type, pin_value)
                    VALUES ('software', 'Nyctophile@2341059')
                """)

            self.cursor.execute("SELECT COUNT(*) FROM pins WHERE pin_type='admin'")
            if self.cursor.fetchone()[0] == 0:
                self.cursor.execute("""
                    INSERT INTO pins (pin_type, pin_value)
                    VALUES ('admin', 'Nyctophile@2543110')
                """)

            self.conn.commit()

            # ================= ENSURE INVOICE FOLDER EXISTS =================
            os.makedirs("Invoices", exist_ok=True)

        except Exception as e:
            logging.error(f"Database creation error: {e}")
            messagebox.showerror("Error", f"Failed to create database: {e}")
            raise


    def create_pin_entry_page(self):
        try:
            self.clear_frame()
            style = ttk.Style(self.root)
            if "clam" in style.theme_names():
                style.theme_use("clam")

          
            self.root.configure(bg="#f8f9fa")

           
            container = ttk.Frame(self.root, padding=40)
            container.place(relx=0.5, rely=0.5, anchor="center")

          
            ttk.Label(container, text="Welcome to Gold Shop System", font=("Segoe UI", 26, "bold")).pack(pady=(0, 10))
            ttk.Label(container, text="Enter your Software PIN to continue", font=("Segoe UI", 12)).pack(pady=(0, 20))

       
            self.pin_entry = ttk.Entry(container, show="*", width=30, font=("Segoe UI", 12))
            self.pin_entry.pack(pady=10, ipady=3)

       
            ttk.Button(container, text="Login", command=self.verify_pin).pack(pady=(15, 10))

          
            ttk.Label(container, text="Â© 2025 Gold Shop | Secure Access Panel", font=("Segoe UI", 9)).pack(pady=(10, 0))
        except Exception as e:
            logging.error(f"PIN Entry UI error: {e}")
            messagebox.showerror("Error", f"Failed to load login screen: {e}")


    def verify_pin(self):
        try:
            entered_pin = self.pin_entry.get()
            self.cursor.execute("SELECT pin_value FROM pins WHERE pin_type = 'software'")
            correct_pin = self.cursor.fetchone()[0]
            if entered_pin == correct_pin:
                self.create_welcome_page()
            else:
                messagebox.showerror("Error", "Incorrect PIN. Please try again.")
        except Exception as e:
            logging.error(f"Pin verification error: {e}")
            messagebox.showerror("Error", f"Failed to verify pin: {e}")

    
    def create_welcome_page(self):
        self.clear_frame()
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.root.configure(bg="#f4f4f4")

               
        try:
            self.cursor.execute("SELECT shop_name FROM settings LIMIT 1")
            row = self.cursor.fetchone()
            shop_name = row[0] if row and row[0] else "Gold Shop"
        except:
            shop_name = "Gold Shop"

       
        ttk.Label(
            self.root,
            text=f"{shop_name} Dashboard",
            font=("Segoe UI", 22, "bold")
        ).pack(pady=20)

       

        wrap = ttk.Frame(self.root, padding=20)
        wrap.pack(fill=tk.BOTH, expand=True)


        ttk.Label(wrap, text="Choose an Option:", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 10))

        btn_frame = ttk.Frame(wrap)
        btn_frame.pack(fill=tk.X, pady=20)

        ttk.Button(btn_frame,text="Register Customer",command=self.open_register_customer).grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        ttk.Button(btn_frame,text="Load Customer",command=self.open_load_customer).grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        ttk.Button(btn_frame,text="Make Payment",command=self.open_make_payment).grid(row=0, column=2, padx=10, pady=10, sticky="ew")
        ttk.Button(btn_frame,text="Admin Dashboard",command=self.open_admin_page).grid(row=0, column=3, padx=10, pady=10, sticky="ew")
        for i in range(4):
            btn_frame.grid_columnconfigure(i, weight=1)

        footer = ttk.LabelFrame(wrap, text="Help & Information", padding=10)
        footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(20, 0))
        ttk.Button(footer, text="Contact Us", command=self.open_contact_us_page).pack(side=tk.LEFT, padx=8)
        ttk.Button(footer, text="Support Us", command=self.open_support_us_page).pack(side=tk.LEFT, padx=8)
        ttk.Button(footer, text="Work Flow", command=self.open_workflow_page).pack(side=tk.LEFT, padx=8)




    def open_register_customer(self):
        try:
            self.clear_frame()
            RegisterCustomer(self.root, self.conn, self.create_welcome_page)
        except Exception as e:
            logging.error(f"Open register customer error: {e}")
            messagebox.showerror("Error", f"Failed to open register customer page: {e}")

    def open_load_customer(self):
        try:
            self.clear_frame()
            LoadCustomer(self.root, self.conn, self.create_welcome_page, self.open_invoice_page)
        except Exception as e:
            logging.error(f"Open load customer error: {e}")
            messagebox.showerror("Error", f"Failed to open load customer page: {e}")

    def open_invoice_page(self, customer_details):
        try:
            self.clear_frame()
            if _normalize_customer_role(customer_details.get("role")) == "BusinessMan":
                BusinessInvoicePage(self.root, customer_details, self.create_welcome_page)
            else:
                InvoicePage(self.root, customer_details, self.create_welcome_page)
        except Exception as e:
            logging.error(f"Open invoice page error: {e}")
            messagebox.showerror("Error", f"Failed to open invoice page: {e}")

    def open_make_payment(self):
        try:
            self.clear_frame()
            MakePaymentPage(self.root, self.conn, self.create_welcome_page)
        except Exception as e:
            logging.error(f"Open make payment error: {e}")
            messagebox.showerror("Error", f"Failed to open make payment page: {e}")

    def open_admin_page(self):
        try:
            def verify_admin_pin():
                entered_pin = pin_entry.get()
                self.cursor.execute("SELECT pin_value FROM pins WHERE pin_type = 'admin'")
                correct_pin = self.cursor.fetchone()[0]
                if entered_pin == correct_pin:
                    admin_pin_window.destroy()
                    AdminLoginPage(self.root, self.conn, self.create_welcome_page)
                else:
                    messagebox.showerror("Error", "Incorrect Admin PIN. Please try again.")

            admin_pin_window = tk.Toplevel(self.root)
            admin_pin_window.title("Admin Login")
            admin_pin_window.geometry("300x150")

            tk.Label(admin_pin_window, text="Enter Admin PIN:", font=("Arial", 12)).pack(pady=10)
            pin_entry = tk.Entry(admin_pin_window, show="*", width=20, font=("Arial", 12))
            pin_entry.pack(pady=5)

            tk.Button(admin_pin_window, text="Submit", command=verify_admin_pin).pack(pady=10)
            tk.Button(admin_pin_window, text="Cancel", command=admin_pin_window.destroy).pack(pady=5)
        except Exception as e:
            logging.error(f"Open admin page error: {e}")
            messagebox.showerror("Error", f"Failed to open admin page: {e}")

    def open_contact_us_page(self):
        self.clear_frame()
        ttk.Label(self.root, text="Contact Us", font=("Segoe UI", 20, "bold")).pack(pady=20)
        card = ttk.LabelFrame(self.root, text="Tech Department Contact", padding=16)
        card.pack(fill=tk.X, padx=30, pady=10)

        details = [
            ("Department", ADMIN_SUPPORT_CONTACT["Department"]),
            ("Support Type", ADMIN_SUPPORT_CONTACT["Support Type"]),
            ("Contact Number", ADMIN_SUPPORT_CONTACT["Contact Number"]),
            ("Email", ADMIN_SUPPORT_CONTACT["Email"]),
            ("Address", ADMIN_SUPPORT_CONTACT["Address"]),
            ("Working Hours", ADMIN_SUPPORT_CONTACT["Working Hours"]),
        ]
        for label, value in details:
            ttk.Label(card, text=f"{label}:", width=18, anchor="w").pack(anchor="w")
            ttk.Label(card, text=str(value), anchor="w", wraplength=900, justify="left").pack(anchor="w", pady=(0, 8))

        ttk.Label(
            self.root,
            text="This support is only for shop admins facing software issues.",
            font=("Segoe UI", 10)
        ).pack(anchor="w", padx=34, pady=(6, 12))
        ttk.Button(self.root, text="Back to Dashboard", command=self.create_welcome_page).pack(pady=10)

    def open_support_us_page(self):
        self.clear_frame()
        ttk.Label(self.root, text="Support Us", font=("Segoe UI", 20, "bold")).pack(pady=20)

        card = ttk.LabelFrame(self.root, text="How You Can Support", padding=16)
        card.pack(fill=tk.X, padx=30, pady=10)
        lines = [
            "1. Keep software settings updated from Admin Dashboard.",
            "2. Take regular backups of InvoiceSystem.db and Invoices folder.",
            "3. Report issues with exact screenshot, date, and invoice number.",
            "4. Share suggestions to improve billing speed and accuracy.",
        ]
        for line in lines:
            ttk.Label(card, text=line, anchor="w").pack(anchor="w", pady=3)

        ttk.Label(
            self.root,
            text="Thank you for supporting continuous improvements for this software.",
            font=("Segoe UI", 10)
        ).pack(anchor="w", padx=34, pady=(6, 12))
        ttk.Button(self.root, text="Back to Dashboard", command=self.create_welcome_page).pack(pady=10)

    def open_workflow_page(self):
        self.clear_frame()
        ttk.Label(self.root, text="Software Work Flow", font=("Segoe UI", 20, "bold")).pack(pady=20)

        card = ttk.LabelFrame(self.root, text="Step-by-Step Process", padding=16)
        card.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        steps = [
            "1. Register customer details (name, mobile, role, city).",
            "2. Load customer and create invoice (Retail or BusinessMan).",
            "3. Add item details and optional exchange items.",
            "4. Verify totals, old balance, GST, and fine calculations.",
            "5. Open Make Payment and complete payment entry.",
            "6. Save invoice, update balances, and print invoice.",
            "7. Use Admin Dashboard for stock, settings, reports, and user history.",
            "8. Use User History to review all invoices customer-wise.",
        ]
        for line in steps:
            ttk.Label(card, text=line, anchor="w", justify="left", wraplength=980).pack(anchor="w", pady=3)

        ttk.Button(self.root, text="Back to Dashboard", command=self.create_welcome_page).pack(pady=10)

        

            

    def clear_frame(self):
        try:
            for widget in self.root.winfo_children():
                widget.destroy()
        except Exception as e:
            logging.error(f"Clear frame error: {e}")
            messagebox.showerror("Error", f"Failed to clear frame: {e}")

class RegisterCustomer:
    def __init__(self, root, conn, go_back_callback):
        self.root = root
        self.conn = conn
        self.go_back_callback = go_back_callback
        self.create_register_page()

    
    def create_register_page(self):
        style = ttk.Style(self.root)
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass
        self.root.configure(bg="#f4f4f4")
        container = ttk.Frame(self.root, padding=20)
        container.place(relx=0.5, rely=0.35, anchor="center")

        ttk.Label(container, text="Register Customer", font=("Segoe UI", 18, "bold")).pack(pady=10)
        form = ttk.Frame(container, padding=10)
        form.pack()

        self.name_entry = self.create_entry_ttk(form, "Customer Name")
        self.city_entry = self.create_entry_ttk(form, "City")
        self.mobile_entry = self.create_entry_ttk(form, "Mobile Number")
        role_frame = ttk.Frame(form)
        role_frame.pack(pady=6, anchor='w', fill=tk.X)
        ttk.Label(role_frame, text="Customer Role", width=20, anchor='w').pack(side=tk.LEFT)
        self.role_var = tk.StringVar(value="Customer")
        self.role_combo = ttk.Combobox(
            role_frame,
            textvariable=self.role_var,
            values=["Customer", "BusinessMan"],
            state="readonly",
            width=28
        )
        self.role_combo.pack(side=tk.LEFT, padx=6)

        btn_frame = ttk.Frame(container)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Register", command=self.register_customer).grid(row=0, column=0, padx=8)
        ttk.Button(btn_frame, text="Back", command=self.go_back_callback).grid(row=0, column=1, padx=8)


    def create_entry_ttk(self, parent, label_text):
        frame = ttk.Frame(parent)
        frame.pack(pady=6, anchor='w', fill=tk.X)
        ttk.Label(frame, text=label_text, width=20, anchor='w').pack(side=tk.LEFT)
        entry = ttk.Entry(frame, width=30)
        entry.pack(side=tk.LEFT, padx=6)
        return entry


    def register_customer(self):
        name = self.name_entry.get()
        city = self.city_entry.get()
        mobile = self.mobile_entry.get()
        role = self.role_var.get().strip() or "Customer"

        if not name or not city or not mobile:
            messagebox.showerror("Error", "All fields are required")
            return

       
        current_date = datetime.now()
        customer_code = f"cust{current_date.year}{current_date.month:02d}{self.get_next_customer_id():03d}"
        try:
            self.conn.execute(
                "INSERT INTO customers (name, city, mobile, customer_code, role) VALUES (?, ?, ?, ?, ?)",
                (name, city, mobile, customer_code, role)
            )
            self.conn.commit()
            messagebox.showinfo("Success", "Customer registered successfully")
            self.go_back_callback()
        except sqlite3.IntegrityError:
            messagebox.showerror("Error", "Mobile number already registered")

    def get_next_customer_id(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT MAX(id) FROM customers")
        max_id = cursor.fetchone()[0]
        return (max_id or 0) + 1

class LoadCustomer:
    def __init__(self, root, conn, go_back_callback, open_invoice_page_callback):
        self.root = root
        self.conn = conn
        self.go_back_callback = go_back_callback
        self.open_invoice_page_callback = open_invoice_page_callback
        self.create_load_page()

    
    def create_load_page(self):
        style = ttk.Style(self.root)
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass
        self.root.configure(bg="#f4f4f4")
        container = ttk.Frame(self.root, padding=20)
        container.place(relx=0.5, rely=0.35, anchor="center")

        ttk.Label(container, text="Load Customer", font=("Segoe UI", 18, "bold")).pack(pady=10)
        form = ttk.Frame(container, padding=10)
        form.pack()

        self.mobile_entry = self.create_entry_ttk_load(form, "Mobile Number")
        btn_frame = ttk.Frame(container)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Load", command=self.load_customer).grid(row=0, column=0, padx=8)
        ttk.Button(btn_frame, text="Back", command=self.go_back_callback).grid(row=0, column=1, padx=8)


    def create_entry_ttk_load(self, parent, label_text):
        frame = ttk.Frame(parent)
        frame.pack(pady=6, anchor='w', fill=tk.X)
        ttk.Label(frame, text=label_text).pack(side=tk.LEFT)
        entry = ttk.Entry(frame, width=30)
        entry.pack(side=tk.LEFT, padx=6)
        return entry


    def load_customer(self):
        mobile = self.mobile_entry.get()

        if not mobile:
            messagebox.showerror("Error", "Mobile number is required")
            return

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT name, city, mobile, customer_code,
                   COALESCE(role, 'Customer'),
                   COALESCE(balance, 0),
                   COALESCE(fine_balance, 0)
            FROM customers
            WHERE mobile = ?
        """, (mobile,))
        customer = cursor.fetchone()

        if customer:
            customer_details = {
                "name": customer[0],
                "city": customer[1],
                "mobile": customer[2],
                "customer_code": customer[3],
                "role": customer[4] or "Customer",
                "balance": float(customer[5] or 0),
                "fine_balance": float(customer[6] or 0),
                "conn": self.conn  
            }

            
            cursor.execute("SELECT MAX(CAST(SUBSTR(invoice_number, -3) AS INTEGER)) FROM invoices WHERE customer_code = ?", (customer[3],))
            db_max_invoice_number = cursor.fetchone()[0] or 0

            
            csv_max_invoice_number = 0
            csv_file_path = os.path.join("Invoices", "invoices.csv")
            if os.path.exists(csv_file_path):
                with open(csv_file_path, mode="r", newline="", encoding="utf-8") as file:
                    csv_reader = csv.reader(file)
                    next(csv_reader)  
                    for row in csv_reader:
                        if row[1] == customer[3]:  
                            csv_invoice_number = int(row[2][-3:]) 
                            csv_max_invoice_number = max(csv_max_invoice_number, csv_invoice_number)

            
            next_invoice_number = max(db_max_invoice_number, csv_max_invoice_number) + 1

            
            current_date = datetime.now()
            invoice_number = f"{current_date.year}{current_date.month:02d}{next_invoice_number:03d}"

            
            while True:
                cursor.execute("SELECT * FROM invoices WHERE invoice_number = ?", (invoice_number,))
                if not cursor.fetchone():
                    break
                next_invoice_number += 1
                invoice_number = f"{current_date.year}{current_date.month:02d}{next_invoice_number:03d}"

            self.open_invoice_page_callback(customer_details)
        else:
            messagebox.showerror("Error", "No customer found")

class InvoicePage:
    def __init__(self, root, customer_details, go_back_callback):
        self.root = root
        self.customer_details = customer_details
        self.go_back_callback = go_back_callback

        self.items = []
        self.exchange_items = []
        self.exchange_total = 0.0
        self.items_total = 0.0
        self.grand_total = 0.0
        self.total_amount = 0.0
        self.invoice_number = self.generate_invoice_number()

       
        cursor = self.customer_details["conn"].cursor()
        cursor.execute(
            "SELECT making_charges_22c, making_charges_24c FROM settings LIMIT 1"
        )
        row = cursor.fetchone() or (10.0, 100.0)

       
        self.default_making_22c = float(row[0])
        self.default_making_24c = float(row[1])

        # Temporary values 
        self.temp_making_22c = None
        self.temp_making_24c = None

      
        self.making_dropdown_visible = False
        self.making_dropdown_frame = None
#GST 4
        cursor.execute("""
            SELECT gst_percent, gst_enabled,
                   COALESCE(shop_name, ''),
                   COALESCE(shop_contact, ''),
                   COALESCE(shop_gst_no, '')
            FROM settings
            LIMIT 1
        """)
        gst_row = cursor.fetchone() or (3.0, 1, "Gold Shop", "", "")

        self.gst_percent = float(gst_row[0])
        self.gst_enabled = bool(gst_row[1])
        self.shop_name = gst_row[2] or "Gold Shop"
        self.shop_contact = gst_row[3] or "-"
        self.shop_gst_no = gst_row[4] or "-"

        self.create_invoice_page()

    def generate_invoice_number(self):
        return _next_invoice_number(self.customer_details["conn"])

    def create_invoice_page(self):
        self.page_container, self.page_canvas, self.page_content = _create_scrollable_page(self.root)
        self.display_customer_details()
        self.create_items_table()
        self.create_exchange_section()
        self.create_totals_section()
        self.create_payment_section()

        actions_frame = tk.Frame(self.page_content)
        actions_frame.pack(fill=tk.X, pady=10)
        self.make_payment_button = tk.Button(actions_frame, text="Make Payment", command=self.make_payment)
        self.make_payment_button.pack(pady=5)
        tk.Button(actions_frame, text="Cancel Invoice", command=self.cancel_invoice).pack(pady=5)
        ttk.Button(actions_frame, text="Back", command=self.go_back_callback).pack(pady=5)
        self.update_make_payment_state()
        self.root.after(0, self.refresh_invoice_navigation)

    def update_make_payment_state(self):
        if hasattr(self, "make_payment_button"):
            self.make_payment_button.config(state="normal" if self.items else "disabled")

    def get_old_balance(self):
       
        try:
            return float(self.old_balance_label.cget("text").split(": ")[1])
        except Exception:
            return 0

    def cancel_invoice(self):
        
        messagebox.showinfo("Invoice Canceled", "The invoice has been canceled.")
        self.go_back_callback()

    def fetch_old_balance(self):
        cursor = self.customer_details["conn"].cursor()
        cursor.execute(
            "SELECT balance FROM customers WHERE customer_code = ?",
            (self.customer_details["customer_code"],)
        )
        row = cursor.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def parse_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def display_customer_details(self):
        top_frame = tk.Frame(self.page_content)
        top_frame.pack(side=tk.TOP, fill=tk.X)

        left_frame = tk.Frame(top_frame)
        left_frame.pack(side=tk.LEFT, padx=0, anchor='w')

        tk.Label(left_frame, text=f"Customer Name: {self.customer_details['name']}", font=("Arial", 14)).pack(pady=5, anchor='w')
        tk.Label(left_frame, text=f"City: {self.customer_details['city']}", font=("Arial", 14)).pack(pady=5, anchor='w')
        tk.Label(left_frame, text=f"Mobile Number: {self.customer_details['mobile']}", font=("Arial", 14)).pack(pady=5, anchor='w')
        tk.Label(left_frame, text=f"Shop: {self.shop_name}", font=("Arial", 14)).pack(pady=5, anchor='w')
        tk.Label(left_frame, text=f"Shop Contact: {self.shop_contact}", font=("Arial", 14)).pack(pady=5, anchor='w')
        tk.Label(left_frame, text=f"Shop GST No: {self.shop_gst_no}", font=("Arial", 14)).pack(pady=5, anchor='w')

        right_frame = tk.Frame(top_frame)
        right_frame.pack(side=tk.RIGHT, padx=0, anchor='e')

        tk.Label(right_frame, text=f"Customer Code: {self.customer_details['customer_code']}", font=("Arial", 14)).pack(pady=5, anchor='e')
        tk.Label(right_frame, text=f"Invoice Number: {self.invoice_number}", font=("Arial", 14)).pack(pady=5, anchor='e') 
        tk.Label(right_frame, text=f"Current Date: {datetime.now().strftime('%Y-%m-%d')}", font=("Arial", 14)).pack(pady=5, anchor='e')

        gst_frame = tk.Frame(right_frame)
        gst_frame.pack(anchor='e', pady=5)

        self.invoice_gst_enabled_var = tk.IntVar(value=1 if self.gst_enabled else 0)
        tk.Checkbutton(
            gst_frame,
            text="Enable GST For This Invoice",
            variable=self.invoice_gst_enabled_var,
            command=self.toggle_invoice_gst
        ).pack(anchor='e')

        self.gst_status_label = tk.Label(gst_frame, font=("Arial", 11))
        self.gst_status_label.pack(anchor='e')
        self.update_gst_status_label()

    def update_gst_status_label(self):
        if hasattr(self, "gst_status_label"):
            state_text = "Enabled" if self.gst_enabled else "Disabled"
            self.gst_status_label.config(
                text=f"GST Status: {state_text} ({self.gst_percent:.2f}%)"
            )

    def toggle_invoice_gst(self):
        self.gst_enabled = bool(self.invoice_gst_enabled_var.get())
        self.update_gst_status_label()
        self.recalculate_items()

    def create_items_table(self):
        columns = [
                    "Category",
                    "Product Name",
                    "Quantity",
                    "Net Weight",
                    "Gross Weight",
                    "Rate Per Gram",
                    "Making Charges Per Gram >",
                    "Total Making Charges",
                    "GST %",
                    "GST Amount",
                    "Total Rupees"
                ]

        self.table_frame = tk.Frame(self.page_content)
        self.table_frame.pack(fill=tk.BOTH, expand=True, pady=10)

        for index, col in enumerate(columns):
            if col == "Making Charges Per Gram >":
                tk.Button(self.table_frame,text=col,font=("Arial", 12, "bold"),bg="blue",fg="white",borderwidth=1,relief="solid",activebackground="blue",activeforeground="white",command=self.show_making_charges_dropdown).grid(row=0, column=index, sticky="nsew")
            elif col == "GST %":
                tk.Button(self.table_frame,text=col,font=("Arial", 12, "bold"),bg="blue",fg="white",borderwidth=1,relief="solid",activebackground="blue",activeforeground="white",command=self.show_gst_percent_popup).grid(row=0, column=index, sticky="nsew")
            else:
                tk.Label(self.table_frame,text=col,font=("Arial", 12),bg="blue",fg="white",borderwidth=1,relief="solid").grid(row=0, column=index, sticky="nsew")

        for i in range(len(columns)):
            self.table_frame.grid_columnconfigure(i, weight=1)

        self.add_item_button = tk.Button(
            self.page_content,
            text="Add Item",
            command=self.open_add_item_page
        )
        self.add_item_button.pack(pady=10)

    def create_exchange_section(self):
        self.exchange_frame = tk.Frame(self.page_content)
        self.exchange_frame.pack(fill=tk.X, pady=(0, 10))

        header_frame = tk.Frame(self.exchange_frame)
        header_frame.pack(fill=tk.X)

        tk.Label(
            header_frame,
            text="Exchange Products",
            font=("Arial", 13, "bold")
        ).pack(side=tk.LEFT, padx=10)

        tk.Button(
            header_frame,
            text="Add Exchange Item",
            command=self.add_exchange_row
        ).pack(side=tk.RIGHT, padx=10)

        self.exchange_table_frame = tk.Frame(self.exchange_frame)
        self.exchange_table_frame.pack(fill=tk.X, padx=5, pady=5)

        self.exchange_total_label = tk.Label(
            self.exchange_frame,
            text="Exchange Total: 0.00",
            font=("Arial", 11, "bold")
        )
        self.exchange_total_label.pack(anchor="e", padx=10)

        self.add_exchange_row()

    def add_exchange_row(self, item_data=None):
        item_data = item_data or {}
        row_data = {
            "description_var": tk.StringVar(value=item_data.get("item_description", "")),
            "net_weight_var": tk.StringVar(value=str(item_data.get("net_weight", ""))),
            "purity_var": tk.StringVar(value=str(item_data.get("purity_percent", ""))),
            "rate_var": tk.StringVar(value=str(item_data.get("rate_per_gram", ""))),
            "amount_var": tk.StringVar(value="0.00"),
            "exchange_amount": 0.0,
        }

        for key in ("net_weight_var", "purity_var", "rate_var"):
            row_data[key].trace_add("write", self.on_exchange_value_changed)

        self.exchange_items.append(row_data)
        self.render_exchange_rows()
        self.recalculate_exchange_items()

    def on_exchange_value_changed(self, *args):
        self.recalculate_exchange_items()

    def remove_exchange_row(self, index):
        if 0 <= index < len(self.exchange_items):
            del self.exchange_items[index]

        if not self.exchange_items:
            self.add_exchange_row()
            return

        self.render_exchange_rows()
        self.recalculate_exchange_items()

    def render_exchange_rows(self):
        for widget in self.exchange_table_frame.winfo_children():
            widget.destroy()

        columns = [
            "Old Item Details",
            "Net Weight",
            "Purity %",
            "Rate Per Gram",
            "Exchange Value",
            "Action"
        ]

        for col_index, col_name in enumerate(columns):
            tk.Label(
                self.exchange_table_frame,
                text=col_name,
                font=("Arial", 11, "bold"),
                bg="darkgreen",
                fg="white",
                borderwidth=1,
                relief="solid"
            ).grid(row=0, column=col_index, sticky="nsew")

        for row_index, row_data in enumerate(self.exchange_items, start=1):
            entry = tk.Entry(
                self.exchange_table_frame,
                textvariable=row_data["description_var"]
            )
            entry.grid(row=row_index, column=0, sticky="nsew")
            entry = tk.Entry(
                self.exchange_table_frame,
                textvariable=row_data["net_weight_var"]
            )
            entry.grid(row=row_index, column=1, sticky="nsew")
            entry = tk.Entry(
                self.exchange_table_frame,
                textvariable=row_data["purity_var"]
            )
            entry.grid(row=row_index, column=2, sticky="nsew")
            entry = tk.Entry(
                self.exchange_table_frame,
                textvariable=row_data["rate_var"]
            )
            entry.grid(row=row_index, column=3, sticky="nsew")

            tk.Label(
                self.exchange_table_frame,
                textvariable=row_data["amount_var"],
                borderwidth=1,
                relief="solid"
            ).grid(row=row_index, column=4, sticky="nsew")

            tk.Button(
                self.exchange_table_frame,
                text="Delete",
                command=lambda idx=row_index - 1: self.remove_exchange_row(idx)
            ).grid(row=row_index, column=5, sticky="nsew")

        for col_index in range(len(columns)):
            self.exchange_table_frame.grid_columnconfigure(col_index, weight=1)
        self.refresh_invoice_navigation()

    def recalculate_exchange_items(self):
        total_exchange = 0.0

        for row_data in self.exchange_items:
            net_weight = self.parse_float(row_data["net_weight_var"].get())
            purity_percent = self.parse_float(row_data["purity_var"].get())
            rate_per_gram = self.parse_float(row_data["rate_var"].get())
            exchange_amount = net_weight * (purity_percent / 100.0) * rate_per_gram

            row_data["exchange_amount"] = exchange_amount
            row_data["amount_var"].set(f"{exchange_amount:.2f}")

            if any([
                row_data["description_var"].get().strip(),
                row_data["net_weight_var"].get().strip(),
                row_data["purity_var"].get().strip(),
                row_data["rate_var"].get().strip(),
            ]):
                total_exchange += exchange_amount

        self.exchange_total = total_exchange

        if hasattr(self, "exchange_total_label"):
            self.exchange_total_label.config(
                text=f"Exchange Total: {self.exchange_total:.2f}"
            )

        self.update_totals()

    def get_exchange_entries(self):
        exchange_entries = []

        for index, row_data in enumerate(self.exchange_items, start=1):
            description = row_data["description_var"].get().strip()
            net_weight_text = row_data["net_weight_var"].get().strip()
            purity_text = row_data["purity_var"].get().strip()
            rate_text = row_data["rate_var"].get().strip()

            if not any([description, net_weight_text, purity_text, rate_text]):
                continue

            try:
                net_weight = float(net_weight_text)
                purity_percent = float(purity_text)
                rate_per_gram = float(rate_text)
            except ValueError:
                raise ValueError(f"Exchange row {index} has invalid numeric values.")

            if net_weight <= 0 or purity_percent < 0 or rate_per_gram < 0:
                raise ValueError(f"Exchange row {index} must have valid positive values.")

            exchange_entries.append({
                "item_description": description or f"Old Gold Item {index}",
                "net_weight": net_weight,
                "purity_percent": purity_percent,
                "rate_per_gram": rate_per_gram,
                "exchange_amount": net_weight * (purity_percent / 100.0) * rate_per_gram,
            })

        return exchange_entries


    def show_making_charges_dropdown(self):
        popup = tk.Toplevel(self.root)
        popup.title("Invoice Making Charges")
        popup.geometry("300x200")
        popup.transient(self.root)
        popup.grab_set()

        frame = tk.Frame(popup, padx=15, pady=15)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="22c Making Charges (%):", anchor="w").pack(fill="x", pady=5)
        entry_22c = tk.Entry(frame)
        effective_22c = self.default_making_22c if self.temp_making_22c is None else self.temp_making_22c
        entry_22c.insert(0, str(effective_22c))
        entry_22c.pack(fill="x", pady=5)

        tk.Label(frame, text="24c Making Charges (Rs/gram):", anchor="w").pack(fill="x", pady=5)
        entry_24c = tk.Entry(frame)
        effective_24c = self.default_making_24c if self.temp_making_24c is None else self.temp_making_24c
        entry_24c.insert(0, str(effective_24c))
        entry_24c.pack(fill="x", pady=5)

        def apply_changes():
            try:
                self.temp_making_22c = float(entry_22c.get())
                self.temp_making_24c = float(entry_24c.get())

                # Recalculate all items in current invoice
                self.recalculate_items()

                popup.destroy()
            except ValueError:
                messagebox.showerror("Error", "Enter valid numeric values.")

        tk.Button(frame, text="Apply", command=apply_changes).pack(pady=10)

    def show_gst_percent_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("Update GST % For Invoice")
        popup.geometry("300x150")
        popup.transient(self.root)
        popup.grab_set()

        frame = tk.Frame(popup, padx=15, pady=15)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="GST Percentage (%):", anchor="w").pack(fill="x", pady=5)
        gst_entry = tk.Entry(frame)
        gst_entry.insert(0, f"{self.gst_percent:.2f}")
        gst_entry.pack(fill="x", pady=5)

        def apply_gst_percent():
            try:
                new_gst = float(gst_entry.get())
                if new_gst < 0:
                    raise ValueError

                self.gst_percent = new_gst
                self.update_gst_status_label()
                self.recalculate_items()
                popup.destroy()
            except ValueError:
                messagebox.showerror("Error", "Enter a valid GST percentage (0 or more).")

        tk.Button(frame, text="Apply", command=apply_gst_percent).pack(pady=10)

    def recalculate_items(self):

        if self.temp_making_22c is None:
            self.temp_making_22c = self.default_making_22c

        if self.temp_making_24c is None:
            self.temp_making_24c = self.default_making_24c

        for item in self.items:

            rate = float(item.get("Rate Per Gram", 0))
            qty = float(item.get("Quantity", 0))
            net_wt = float(item.get("Net Weight", 0))
            category = item.get("Category", "22c")

            # ---- Making Charges Per Gram ----
            if category == "22c":
                making_per_gram = rate * (self.temp_making_22c / 100)
            else:
                making_per_gram = self.temp_making_24c

            # ---- Base Calculations ----
            base_total = rate * net_wt * qty
            total_making = making_per_gram * net_wt * qty
            total_rupees = base_total + total_making

            # ---- GST ----
            if self.gst_enabled:
                gst_amount = total_rupees * (self.gst_percent / 100)
            else:
                gst_amount = 0

            final_total = total_rupees + gst_amount

            # ---- Store Back ----
            item["Making Charges Per Gram"] = making_per_gram
            item["Total Making Charges"] = total_making
            item["Total Rupees"] = total_rupees
            item["GST %"] = self.gst_percent if self.gst_enabled else 0
            item["GST Amount"] = gst_amount
            item["Final Total"] = final_total

        self.update_items_table()
        self.recalculate_exchange_items()

    def create_payment_section(self):
       
        payment_frame = tk.Frame(self.page_content)
        payment_frame.pack(fill=tk.X, pady=10)

        
        old_balance = self.fetch_old_balance()

        
        self.old_balance_label = tk.Label(payment_frame, text=f"Old Balance: {old_balance:.2f}", font=("Arial", 12))
        self.old_balance_label.pack(side=tk.LEFT, padx=10)

        
        self.old_balance = old_balance
        self.recalculate_items()

        
    def make_payment(self):
        if not self.items:
            messagebox.showerror("Error", "Add at least one item to this invoice. Use Make Payment page for old balance payments.")
            return

        payment_window = tk.Toplevel(self.root)
        payment_window.title("Make Payment")
        payment_window.geometry("300x200")
        payment_window.grab_set()

        tk.Label(payment_window, text="Enter Payment Amount:", font=("Arial", 12)).pack(pady=10)

        payment_entry = tk.Entry(payment_window, width=20)
        payment_entry.pack(pady=5)

        # ðŸ”¹ Calculate default grand total
        self.recalculate_items()
        grand_total = self.calculate_invoice_summary()["grand_total"]
        payment_entry.insert(0, str(round(grand_total, 2)))

        def process_payment():
            try:
                payment_amount = float(payment_entry.get())

                # ðŸ”¹ Recalculate again before saving
                exchange_entries = self.get_exchange_entries()
                self.recalculate_items()
                summary = self.calculate_invoice_summary()
                items_total = summary["items_total"]
                exchange_total = summary["exchange_total"]
                old_balance = summary["old_balance"]
                grand_total = summary["grand_total"]

                remaining_balance = grand_total - payment_amount
                if remaining_balance < 0:
                    remaining_balance = 0

                conn = self.customer_details["conn"]
                cursor = conn.cursor()

                # ðŸ”¥ STOCK VALIDATION FIRST
                for item in self.items:
                    stock_id = item.get("Stock ID")
                    sold_qty = float(item["Quantity"])
                    sold_net = sold_qty * float(item["Net Weight"])
                    sold_gross = sold_qty * float(item["Gross Weight"])

                    if stock_id is None:
                        messagebox.showerror(
                            "Stock Error",
                            f"Missing stock selection for {item['Product Name']}. Re-add this item from stock."
                        )
                        conn.rollback()
                        return

                    cursor.execute("""
                        SELECT product_name, quantity, net_weight, gross_weight
                        FROM stock
                        WHERE id = ?
                    """, (stock_id,))
                    stock_row = cursor.fetchone()

                    if not stock_row:
                        messagebox.showerror(
                            "Stock Error",
                            f"Stock ID {stock_id} for {item['Product Name']} was not found."
                        )
                        conn.rollback()
                        return

                    _, available_qty, available_net, available_gross = stock_row
                    if (
                        available_qty < sold_qty
                        or available_net < sold_net
                        or available_gross < sold_gross
                    ):
                        messagebox.showerror(
                            "Stock Error",
                            f"Not enough stock in Stock ID {stock_id} for {item['Product Name']}."
                        )
                        conn.rollback()
                        return

                purchase_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                invoice_date = purchase_timestamp
                updated_at = purchase_timestamp

                # Reserve invoice number at save-time to avoid collisions across counters.
                inserted_master = False
                for _ in range(25):
                    candidate_invoice_number = _next_invoice_number(conn)
                    try:
                        cursor.execute("""
                            INSERT INTO invoices (
                                customer_code,
                                invoice_number,
                                invoice_date,
                                updated_at,
                                invoice_type,
                                customer_role,
                                items_total,
                                exchange_total,
                                old_balance_included,
                                grand_total,
                                amount_paid,
                                remaining_balance
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            self.customer_details["customer_code"],
                            candidate_invoice_number,
                            invoice_date,
                            updated_at,
                            "retail",
                            self.customer_details.get("role", "Customer"),
                            items_total,
                            exchange_total,
                            old_balance,
                            grand_total,
                            payment_amount,
                            remaining_balance
                        ))
                        self.invoice_number = candidate_invoice_number
                        inserted_master = True
                        break
                    except sqlite3.IntegrityError as exc:
                        conn.rollback()
                        if "UNIQUE constraint failed: invoices.invoice_number" in str(exc):
                            continue
                        raise

                if not inserted_master:
                    raise ValueError("Unable to reserve a unique invoice number. Please try again.")

                # ðŸ”¹ Insert invoice details + deduct stock
                for item in self.items:
                    stock_id = item["Stock ID"]

                    cursor.execute("""
                        INSERT INTO invoice_details (
                            customer_code,
                            invoice_number,
                            invoice_date,
                            customer_name,
                            mobile,
                            product_name,
                            quantity,
                            net_weight,
                            gross_weight,
                            rate_per_gram,
                            making_charges_per_gram,
                            total_making_charges,
                            total_rupees,
                            gst_percent,
                            gst_amount,
                            final_total,
                            amount_paid,
                            remaining_balance,
                            category
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        self.customer_details["customer_code"],
                        self.invoice_number,
                        invoice_date,
                        self.customer_details["name"],
                        self.customer_details["mobile"],
                        item["Product Name"],
                        item["Quantity"],
                        item["Net Weight"],
                        item["Gross Weight"],
                        item["Rate Per Gram"],
                        item["Making Charges Per Gram"],
                        item["Total Making Charges"],
                        item["Total Rupees"],
                        item.get("GST %", 0),
                        item.get("GST Amount", 0),
                        item.get("Final Total", 0),
                        payment_amount,
                        remaining_balance,
                        item["Category"]
                    ))

                    # ðŸ”¥ Deduct stock
                    sold_qty = float(item["Quantity"])
                    sold_net = sold_qty * float(item["Net Weight"])
                    sold_gross = sold_qty * float(item["Gross Weight"])

                    cursor.execute("""
                        UPDATE stock
                        SET quantity = quantity - ?,
                            net_weight = net_weight - ?,
                            gross_weight = gross_weight - ?
                        WHERE id = ?
                    """, (
                        sold_qty,
                        sold_net,
                        sold_gross,
                        stock_id
                    ))

                for exchange_item in exchange_entries:
                    cursor.execute("""
                        INSERT INTO exchange_details (
                            customer_code,
                            invoice_number,
                            invoice_date,
                            item_description,
                            net_weight,
                            purity_percent,
                            rate_per_gram,
                            exchange_amount
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        self.customer_details["customer_code"],
                        self.invoice_number,
                        invoice_date,
                        exchange_item["item_description"],
                        exchange_item["net_weight"],
                        exchange_item["purity_percent"],
                        exchange_item["rate_per_gram"],
                        exchange_item["exchange_amount"]
                    ))

                # ðŸ”¹ Update customer balance
                cursor.execute("""
                    UPDATE customers
                    SET balance = ?
                    WHERE customer_code = ?
                """, (
                    remaining_balance,
                    self.customer_details["customer_code"]
                ))

                cursor.execute("""
                    UPDATE invoices
                    SET remaining_balance = ?,
                        updated_at = ?
                    WHERE customer_code = ?
                      AND invoice_number <> ?
                """, (
                    remaining_balance,
                    updated_at,
                    self.customer_details["customer_code"],
                    self.invoice_number
                ))

                cursor.execute("""
                    UPDATE invoice_details
                    SET remaining_balance = ?
                    WHERE customer_code = ?
                      AND invoice_number <> ?
                """, (
                    remaining_balance,
                    self.customer_details["customer_code"],
                    self.invoice_number
                ))

                conn.commit()

                # ðŸ”¹ Export CSV
                try:
                    CSVUtils.export_master_csv(conn)
                except Exception as e:
                    logging.error(f"CSV export error: {e}")


                try:
                    refresh_customer_invoice_files(conn, self.customer_details["customer_code"])
                except Exception as e:
                    logging.error(f"Invoice text refresh error: {e}")
                # ðŸ”¹ Print invoice
                try:
                    print_invoice_for(conn, self.invoice_number)
                except Exception as e:
                    logging.error(f"Print error: {e}")

                messagebox.showinfo(
                    "Payment Successful",
                    f"Grand Total: {grand_total:.2f}\n"
                    f"Exchange Total: {exchange_total:.2f}\n"
                    f"Paid: {payment_amount:.2f}\n"
                    f"Remaining Balance: {remaining_balance:.2f}"
                )

                payment_window.destroy()

                if callable(self.go_back_callback):
                    self.go_back_callback()

            except ValueError as e:
                try:
                    self.customer_details["conn"].rollback()
                except Exception:
                    pass
                messagebox.showerror("Error", str(e) if str(e) else "Enter valid payment amount.")
            except Exception as e:
                try:
                    self.customer_details["conn"].rollback()
                except Exception:
                    pass
                logging.error(f"Payment processing error: {e}")
                messagebox.showerror("Error", f"Payment failed: {e}")

        tk.Button(payment_window, text="Submit", command=process_payment).pack(pady=10)
        tk.Button(payment_window, text="Cancel", command=payment_window.destroy).pack(pady=5)
  
    
    
    def open_add_item_page(self):
        AddItemPage(self.root, self.add_item, self.create_invoice_page)

    def add_item(self, item_details):
        self.items.append(item_details)
        self.recalculate_items()
        self.update_make_payment_state()
        self.update_items_table()
        self.update_totals()
        self.update_make_payment_state()

    def update_items_table(self):
        for widget in self.table_frame.winfo_children():
            widget.destroy()

        columns = [
                    "Category",
                    "Product Name",
                    "Quantity",
                    "Net Weight (per qty)",
                    "Gross Weight (per qty)",
                    "Rate Per Gram",
                    "Making Charges Per Gram >",
                    "Total Making Charges",
                    "GST %",
                    "GST Amount",
                    "Total Rupees"
]
      
        for i, col in enumerate(columns):
            if col == "Making Charges Per Gram >":
                btn = tk.Button(
                    self.table_frame,
                    text=col,
                    font=("Arial", 12),
                    bg="blue",
                    fg="white",
                    borderwidth=1,
                    relief="solid",
                    command=self.show_making_charges_dropdown)
                btn.grid(row=0, column=i, sticky="nsew")
            elif col == "GST %":
                btn = tk.Button(
                    self.table_frame,
                    text=col,
                    font=("Arial", 12),
                    bg="blue",
                    fg="white",
                    borderwidth=1,
                    relief="solid",
                    command=self.show_gst_percent_popup
                )
                btn.grid(row=0, column=i, sticky="nsew")
            else:
                tk.Label(
                    self.table_frame,
                    text=col,
                    font=("Arial", 12),
                    bg="blue",
                    fg="white",
                    borderwidth=1,
                    relief="solid"
                ).grid(row=0, column=i, sticky="nsew")

       
        for i, item in enumerate(self.items):
            values = [
                item.get("Category", ""),
                item.get("Product Name", ""),
                item.get("Quantity", ""),
                item.get("Net Weight", ""),
                item.get("Gross Weight", ""),
                item.get("Rate Per Gram", ""),
                item.get("Making Charges Per Gram", ""),
                item.get("Total Making Charges", ""),
                item.get("GST %", 0),
                item.get("GST Amount", 0),
                item.get("Final Total", item.get("Total Rupees", 0))
]

            for j, value in enumerate(values):
                lbl = tk.Label(
                    self.table_frame,
                    text=value,
                    font=("Arial", 12),
                    borderwidth=1,
                    relief="solid"
                )
                lbl.grid(row=i + 1, column=j, sticky="nsew")

              
                lbl.bind(
                    "<Double-1>",
                    lambda e, index=i: self.edit_item(index)
                )

    
        for i in range(len(columns)):
            self.table_frame.grid_columnconfigure(i, weight=1)
        self.refresh_invoice_navigation()



    def edit_item(self, index):
        """Open the edit dialog for the selected row and update the in-memory items list."""
        try:
            if index < 0 or index >= len(self.items):
                return

            current_item = self.items[index]

            def save_callback(updated_item):

   
                if "Category" not in updated_item:
                    updated_item["Category"] = self.items[index].get("Category", "22c")
                if "Stock ID" not in updated_item and "Stock ID" in self.items[index]:
                    updated_item["Stock ID"] = self.items[index]["Stock ID"]

              
                self.items[index] = updated_item

              
                self.recalculate_items()

    # update_items_table() and update_totals()
    # are already called inside recalculate_items()

            EditItemPage(self.root, current_item, save_callback)
        except Exception as e:
            logging.error(f"Error while editing item at index {index}: {e}")
            messagebox.showerror("Error", "Unable to edit this item. Please try again.")
   
    def create_totals_section(self):
        self.totals_frame = tk.Frame(self.page_content)
        self.totals_frame.pack(fill=tk.X, pady=10)

        total_quantity = sum(item["Quantity"] for item in self.items)
        total_net_weight = sum(
            item["Quantity"] * item["Net Weight"]
            for item in self.items
        )
        total_gross_weight = sum(
            item["Quantity"] * item["Gross Weight"]
            for item in self.items
        )

        per_qty_net_weight = (
            self.items[0]["Net Weight"] if self.items else 0
        )
        per_qty_gross_weight = (
            self.items[0]["Gross Weight"] if self.items else 0
        )

        summary = self.calculate_invoice_summary()
        self.items_total = summary["items_total"]
        self.exchange_total = summary["exchange_total"]
        self.old_balance = summary["old_balance"]
        self.grand_total = summary["grand_total"]
        self.total_amount = self.grand_total
        old_balance = getattr(self, "old_balance", 0.0)
        total_amount = max(0.0, self.items_total + old_balance - self.exchange_total)

        self.totals_grid = tk.Frame(self.totals_frame)
        self.totals_grid.pack(fill=tk.X, padx=10)

        self.total_quantity_label = tk.Label(
            self.totals_grid,
            text=f"Total Quantity: {total_quantity}",
            font=("Arial", 12),
        )
        self.total_quantity_label.grid(row=0, column=0, padx=10, pady=4, sticky="w")

        self.total_net_weight_label = tk.Label(
            self.totals_grid,
            text=(
                f"Net Weight per qty: {per_qty_net_weight} | "
                f"Total Net Weight: {total_net_weight}"
            ),
            font=("Arial", 12),
        )
        self.total_net_weight_label.grid(row=0, column=1, padx=10, pady=4, sticky="w")

        self.total_gross_weight_label = tk.Label(
            self.totals_grid,
            text=(
                f"Gross Weight per qty: {per_qty_gross_weight} | "
                f"Total Gross Weight: {total_gross_weight}"
            ),
            font=("Arial", 12),
        )
        self.total_gross_weight_label.grid(row=0, column=2, padx=10, pady=4, sticky="w")

        self.items_total_label = tk.Label(
            self.totals_grid,
            text=f"Items Total: {self.items_total:.2f}",
            font=("Arial", 12),
        )
        self.items_total_label.grid(row=1, column=0, padx=10, pady=4, sticky="w")

        self.exchange_amount_label = tk.Label(
            self.totals_grid,
            text=f"Exchange Less: {self.exchange_total:.2f}",
            font=("Arial", 12),
        )
        self.exchange_amount_label.grid(row=1, column=1, padx=10, pady=4, sticky="w")

        self.old_balance_total_label = tk.Label(
            self.totals_grid,
            text=f"Old Balance Included: {old_balance:.2f}",
            font=("Arial", 12),
        )
        self.old_balance_total_label.grid(row=1, column=2, padx=10, pady=4, sticky="w")

        self.total_amount_label = tk.Label(
            self.totals_grid,
            text=f"Total Amount: {total_amount:.2f}",
            font=("Arial", 12),
        )
        self.total_amount_label.grid(row=2, column=0, padx=10, pady=4, sticky="w")

        for column_index in range(3):
            self.totals_grid.grid_columnconfigure(column_index, weight=1)

    def calculate_invoice_summary(self):
        items_total = sum(item.get("Final Total", 0) for item in self.items)
        exchange_total = 0.0

        for row_data in self.exchange_items:
            if any([
                row_data["description_var"].get().strip(),
                row_data["net_weight_var"].get().strip(),
                row_data["purity_var"].get().strip(),
                row_data["rate_var"].get().strip(),
            ]):
                exchange_total += float(row_data.get("exchange_amount", 0))

        old_balance = self.fetch_old_balance()
        grand_total = max(0.0, items_total + old_balance - exchange_total)

        return {
            "items_total": items_total,
            "exchange_total": exchange_total,
            "old_balance": old_balance,
            "grand_total": grand_total,
        }

    def update_totals(self):
        total_quantity = sum(item["Quantity"] for item in self.items)

        total_net_weight = sum(
            item["Quantity"] * item["Net Weight"]
            for item in self.items
        )

        total_gross_weight = sum(
            item["Quantity"] * item["Gross Weight"]
            for item in self.items
        )

        summary = self.calculate_invoice_summary()
        self.items_total = summary["items_total"]
        self.exchange_total = summary["exchange_total"]
        self.old_balance = summary["old_balance"]
        self.grand_total = summary["grand_total"]
        self.total_amount = self.grand_total


        # âœ… DEFINE total_amount PROPERLY

        # âœ… UPDATE LABELS
        if hasattr(self, "old_balance_label"):
            self.old_balance_label.config(
                text=f"Old Balance: {self.old_balance:.2f}"
            )

        if not hasattr(self, "total_quantity_label"):
            return

        self.total_quantity_label.config(
            text=f"Total Quantity: {total_quantity}"
        )

        self.total_net_weight_label.config(
            text=f"Total Net Weight: {total_net_weight:.2f}"
        )

        self.total_gross_weight_label.config(
            text=f"Total Gross Weight: {total_gross_weight:.2f}"
        )

        self.items_total_label.config(
            text=f"Items Total: {self.items_total:.2f}"
        )

        self.exchange_amount_label.config(
            text=f"Exchange Less: {self.exchange_total:.2f}"
        )

        self.old_balance_total_label.config(
            text=f"Old Balance Included: {self.old_balance:.2f}"
        )

        self.total_amount_label.config(
            text=f"Total Amount: {self.total_amount:.2f}"
        )

    def refresh_invoice_navigation(self):
        for widget in _iter_all_widgets(self.page_content):
            _bind_mousewheel_to_canvas(widget, self.page_canvas)
        for widget in _iter_focusable_widgets(self.page_content):
            _bind_mousewheel_to_canvas(widget, self.page_canvas)
            _bind_keyboard_navigation(widget, self.page_content, self.page_canvas, self.page_content)

class BusinessInvoicePage:
    def __init__(self, root, customer_details, go_back_callback):
        self.root = root
        self.customer_details = customer_details
        self.go_back_callback = go_back_callback
        self.items = []
        self.exchange_items = []
        self.exchange_fine_total = 0.0
        self.invoice_number = self.generate_invoice_number()

        cursor = self.customer_details["conn"].cursor()
        cursor.execute("""
            SELECT gst_percent, gst_enabled,
                   COALESCE(shop_name, ''),
                   COALESCE(shop_contact, ''),
                   COALESCE(shop_gst_no, '')
            FROM settings
            LIMIT 1
        """)
        row = cursor.fetchone() or (3.0, 1, "Gold Shop", "", "")
        self.gst_percent = float(row[0])
        self.gst_enabled = bool(row[1])
        self.shop_name = row[2] or "Gold Shop"
        self.shop_contact = row[3] or "-"
        self.shop_gst_no = row[4] or "-"
        self.create_invoice_page()

    def generate_invoice_number(self):
        return _next_invoice_number(self.customer_details["conn"])

    def parse_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def fetch_old_balance(self):
        cursor = self.customer_details["conn"].cursor()
        cursor.execute(
            "SELECT COALESCE(balance, 0) FROM customers WHERE customer_code = ?",
            (self.customer_details["customer_code"],)
        )
        row = cursor.fetchone()
        return float(row[0]) if row else 0.0

    def fetch_old_fine_balance(self):
        cursor = self.customer_details["conn"].cursor()
        cursor.execute(
            "SELECT COALESCE(fine_balance, 0) FROM customers WHERE customer_code = ?",
            (self.customer_details["customer_code"],)
        )
        row = cursor.fetchone()
        return float(row[0]) if row else 0.0

    def fetch_last_business_invoice_carry_forward(self):
        cursor = self.customer_details["conn"].cursor()
        cursor.execute(
            """
            SELECT COALESCE(carry_forward_fine, 0)
            FROM invoices
            WHERE customer_code = ?
              AND COALESCE(invoice_type, 'retail') = 'business'
            ORDER BY id DESC
            LIMIT 1
            """,
            (self.customer_details["customer_code"],)
        )
        row = cursor.fetchone()
        return float(row[0] or 0) if row else 0.0

    def fetch_old_balances(self):
        old_balance_rs = self.fetch_old_balance()
        old_balance_fine = self.fetch_old_fine_balance()
        if old_balance_fine <= 0:
            old_balance_fine = self.fetch_last_business_invoice_carry_forward()
        return max(0.0, old_balance_rs), max(0.0, old_balance_fine)

    def create_invoice_page(self):
        self.page_container, self.page_canvas, self.page_content = _create_scrollable_page(self.root)
        self.display_customer_details()
        self.create_items_table()
        self.create_exchange_section()
        self.create_totals_section()
        actions_frame = tk.Frame(self.page_content)
        actions_frame.pack(fill=tk.X, pady=10)
        self.make_payment_button = tk.Button(actions_frame, text="Make Payment", command=self.make_payment)
        self.make_payment_button.pack(pady=5)
        tk.Button(actions_frame, text="Cancel Invoice", command=self.cancel_invoice).pack(pady=5)
        ttk.Button(actions_frame, text="Back", command=self.go_back_callback).pack(pady=5)
        self.update_make_payment_state()
        self.root.after(0, self.refresh_invoice_navigation)

    def update_make_payment_state(self):
        if hasattr(self, "make_payment_button"):
            summary = self.calculate_invoice_summary()
            has_sale_items = bool(self.items)
            has_exchange_payment = summary.get("exchange_fine_total", 0.0) > 0
            has_old_balance = (
                summary.get("old_balance", 0.0) > 0
                or summary.get("old_balance_fine", 0.0) > 0
            )
            can_pay = has_sale_items or has_exchange_payment or has_old_balance
            self.make_payment_button.config(state="normal" if can_pay else "disabled")

    def display_customer_details(self):
        top_frame = tk.Frame(self.page_content)
        top_frame.pack(side=tk.TOP, fill=tk.X)

        left_frame = tk.Frame(top_frame)
        left_frame.pack(side=tk.LEFT, anchor='w')
        tk.Label(left_frame, text=f"Customer Name: {self.customer_details['name']}", font=("Arial", 14)).pack(pady=5, anchor='w')
        tk.Label(left_frame, text=f"Customer Role: {self.customer_details.get('role', 'BusinessMan')}", font=("Arial", 14)).pack(pady=5, anchor='w')
        tk.Label(left_frame, text=f"City: {self.customer_details['city']}", font=("Arial", 14)).pack(pady=5, anchor='w')
        tk.Label(left_frame, text=f"Mobile Number: {self.customer_details['mobile']}", font=("Arial", 14)).pack(pady=5, anchor='w')
        tk.Label(left_frame, text=f"Shop: {self.shop_name}", font=("Arial", 14)).pack(pady=5, anchor='w')

        right_frame = tk.Frame(top_frame)
        right_frame.pack(side=tk.RIGHT, anchor='e')
        tk.Label(right_frame, text=f"Customer Code: {self.customer_details['customer_code']}", font=("Arial", 14)).pack(pady=5, anchor='e')
        tk.Label(right_frame, text=f"Invoice Number: {self.invoice_number}", font=("Arial", 14)).pack(pady=5, anchor='e')
        tk.Label(right_frame, text=f"Current Date: {datetime.now().strftime('%Y-%m-%d')}", font=("Arial", 14)).pack(pady=5, anchor='e')

        gst_frame = tk.Frame(right_frame)
        gst_frame.pack(anchor='e', pady=5)
        self.invoice_gst_enabled_var = tk.IntVar(value=1 if self.gst_enabled else 0)
        tk.Checkbutton(
            gst_frame,
            text="Enable GST For This Invoice",
            variable=self.invoice_gst_enabled_var,
            command=self.toggle_invoice_gst
        ).pack(anchor='e')
        self.gst_status_label = tk.Label(gst_frame, font=("Arial", 11))
        self.gst_status_label.pack(anchor='e')
        self.update_gst_status_label()

    def update_gst_status_label(self):
        state_text = "Enabled" if self.gst_enabled else "Disabled"
        self.gst_status_label.config(text=f"GST Status: {state_text} ({self.gst_percent:.2f}%)")

    def toggle_invoice_gst(self):
        self.gst_enabled = bool(self.invoice_gst_enabled_var.get())
        self.update_gst_status_label()
        self.recalculate_items()

    def create_items_table(self):
        self.table_frame = tk.Frame(self.page_content)
        self.table_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        tk.Button(self.page_content, text="Add Business Item", command=self.open_add_item_page).pack(pady=10)
        self.update_items_table()

    def open_add_item_page(self):
        BusinessAddItemPage(self.root, self.gst_percent, self.gst_enabled, self.add_item)

    def add_item(self, item_details):
        self.items.append(item_details)
        self.recalculate_items()

    def remove_item(self, index):
        if 0 <= index < len(self.items):
            del self.items[index]
        self.recalculate_items()
        self.update_make_payment_state()

    def update_items_table(self):
        for widget in self.table_frame.winfo_children():
            widget.destroy()

        columns = [
            "Product Name", "Net Weight", "Gross Weight", "Purity / Tunch",
            "WSTG %", "Fine (24K)", "Rate", "Labour", "Amount Before GST",
            "GST %", "GST Amount", "Final Price", "Action"
        ]
        for idx, col in enumerate(columns):
            tk.Label(self.table_frame, text=col, font=("Arial", 11, "bold"), bg="navy", fg="white", borderwidth=1, relief="solid").grid(row=0, column=idx, sticky="nsew")

        for row_idx, item in enumerate(self.items, start=1):
            values = [
                item.get("Product Name", ""),
                f"{item.get('Net Weight', 0):.3f}",
                f"{item.get('Gross Weight', 0):.3f}",
                f"{item.get('Purity', 0):.2f}",
                f"{item.get('Wastage %', 0):.2f}",
                f"{item.get('Fine (24K)', 0):.3f}",
                f"{item.get('Rate Per Gram', 0):.2f}",
                f"{item.get('Labour', 0):.2f}",
                f"{item.get('Total Rate', 0):.2f}",
                f"{item.get('GST %', 0):.2f}",
                f"{item.get('GST Amount', 0):.2f}",
                f"{item.get('Final Price', 0):.2f}",
            ]
            for col_idx, value in enumerate(values):
                label = tk.Label(self.table_frame, text=value, borderwidth=1, relief="solid")
                label.grid(row=row_idx, column=col_idx, sticky="nsew")
                label.bind("<Double-1>", lambda _event, index=row_idx - 1: self.edit_item(index))
            tk.Button(self.table_frame, text="Delete", command=lambda index=row_idx - 1: self.remove_item(index)).grid(row=row_idx, column=len(values), sticky="nsew")

        for idx in range(len(columns)):
            self.table_frame.grid_columnconfigure(idx, weight=1)
        self.refresh_invoice_navigation()

    def create_exchange_section(self):
        self.exchange_frame = tk.Frame(self.page_content)
        self.exchange_frame.pack(fill=tk.X, pady=(0, 10))

        header_frame = tk.Frame(self.exchange_frame)
        header_frame.pack(fill=tk.X)
        tk.Label(header_frame, text="Business Exchange Items", font=("Arial", 13, "bold")).pack(side=tk.LEFT, padx=10)
        tk.Button(header_frame, text="Add Exchange Item", command=self.add_exchange_row).pack(side=tk.RIGHT, padx=10)

        self.exchange_table_frame = tk.Frame(self.exchange_frame)
        self.exchange_table_frame.pack(fill=tk.X, padx=5, pady=5)

        self.exchange_summary_label = tk.Label(self.exchange_frame, text="Exchange Total Fine (24K): 0.000", font=("Arial", 11, "bold"))
        self.exchange_summary_label.pack(anchor="e", padx=10)
        self.add_exchange_row()

    def add_exchange_row(self, item_data=None):
        item_data = item_data or {}
        row_data = {
            "product_var": tk.StringVar(value=item_data.get("product_name", "")),
            "net_weight_var": tk.StringVar(value=str(item_data.get("net_weight", ""))),
            "purity_var": tk.StringVar(value=str(item_data.get("purity", ""))),
            "fine_var": tk.StringVar(value="0.000"),
            "fine_24k": 0.0,
        }
        for key in ("net_weight_var", "purity_var"):
            row_data[key].trace_add("write", self.on_exchange_value_changed)
        self.exchange_items.append(row_data)
        self.render_exchange_rows()
        self.recalculate_exchange_items()

    def remove_exchange_row(self, index):
        if 0 <= index < len(self.exchange_items):
            del self.exchange_items[index]
        if not self.exchange_items:
            self.add_exchange_row()
            return
        self.render_exchange_rows()
        self.recalculate_exchange_items()

    def on_exchange_value_changed(self, *args):
        self.recalculate_exchange_items()

    def render_exchange_rows(self):
        for widget in self.exchange_table_frame.winfo_children():
            widget.destroy()
        columns = ["Product Name", "Net Weight", "Purity", "Fine (24K)", "Action"]
        for idx, col in enumerate(columns):
            tk.Label(self.exchange_table_frame, text=col, font=("Arial", 11, "bold"), bg="darkgreen", fg="white", borderwidth=1, relief="solid").grid(row=0, column=idx, sticky="nsew")
        for row_idx, row_data in enumerate(self.exchange_items, start=1):
            entry = tk.Entry(self.exchange_table_frame, textvariable=row_data["product_var"])
            entry.grid(row=row_idx, column=0, sticky="nsew")
            entry = tk.Entry(self.exchange_table_frame, textvariable=row_data["net_weight_var"])
            entry.grid(row=row_idx, column=1, sticky="nsew")
            entry = tk.Entry(self.exchange_table_frame, textvariable=row_data["purity_var"])
            entry.grid(row=row_idx, column=2, sticky="nsew")
            tk.Label(self.exchange_table_frame, textvariable=row_data["fine_var"], borderwidth=1, relief="solid").grid(row=row_idx, column=3, sticky="nsew")
            tk.Button(self.exchange_table_frame, text="Delete", command=lambda index=row_idx - 1: self.remove_exchange_row(index)).grid(row=row_idx, column=4, sticky="nsew")
        for idx in range(len(columns)):
            self.exchange_table_frame.grid_columnconfigure(idx, weight=1)
        self.refresh_invoice_navigation()

    def recalculate_exchange_items(self):
        total_fine = 0.0
        for row_data in self.exchange_items:
            net_weight = self.parse_float(row_data["net_weight_var"].get())
            purity = self.parse_float(row_data["purity_var"].get())
            fine = net_weight * (purity / 100.0)
            row_data["fine_24k"] = fine
            row_data["fine_var"].set(f"{fine:.3f}")
            if any([row_data["product_var"].get().strip(), row_data["net_weight_var"].get().strip(), row_data["purity_var"].get().strip()]):
                total_fine += fine
        self.exchange_fine_total = total_fine
        self.exchange_summary_label.config(text=f"Exchange Total Fine (24K): {self.exchange_fine_total:.3f}")
        self.update_totals()

    def get_exchange_entries(self):
        entries = []
        for idx, row_data in enumerate(self.exchange_items, start=1):
            product_name = row_data["product_var"].get().strip()
            net_weight_text = row_data["net_weight_var"].get().strip()
            purity_text = row_data["purity_var"].get().strip()
            if not any([product_name, net_weight_text, purity_text]):
                continue
            try:
                net_weight = float(net_weight_text)
                purity = float(purity_text)
            except ValueError:
                raise ValueError(f"Business exchange row {idx} has invalid numeric values.")
            entries.append({
                "product_name": product_name or f"Exchange Item {idx}",
                "net_weight": net_weight,
                "purity": purity,
                "fine_24k": net_weight * (purity / 100.0),
            })
        return entries

    def recalculate_items(self):
        for item in self.items:
            effective_purity = item["Purity"] + item["Wastage %"]
            fine_24k = item["Net Weight"] * (effective_purity / 100.0)
            total_rate = (fine_24k * item["Rate Per Gram"]) + item["Labour"]
            gst_amount = total_rate * (self.gst_percent / 100.0) if self.gst_enabled else 0.0
            final_price = total_rate + gst_amount
            item["Fine (24K)"] = fine_24k
            item["Total Rate"] = total_rate
            item["GST %"] = self.gst_percent if self.gst_enabled else 0.0
            item["GST Amount"] = gst_amount
            item["Final Price"] = final_price
        self.update_items_table()
        self.recalculate_exchange_items()
        self.update_make_payment_state()

    def get_reference_rate(self):
        if self.items:
            return float(self.items[-1].get("Rate Per Gram", 0) or 0)
        cursor = self.customer_details["conn"].cursor()
        cursor.execute(
            """
            SELECT COALESCE(rate_per_gram, 0)
            FROM business_invoice_details
            WHERE customer_code = ?
              AND COALESCE(rate_per_gram, 0) > 0
            ORDER BY id DESC
            LIMIT 1
            """,
            (self.customer_details["customer_code"],),
        )
        row = cursor.fetchone()
        if row:
            return float(row[0] or 0)
        return 0.0

    def calculate_invoice_summary(self):
        items_total = sum(item.get("Final Price", 0) for item in self.items)
        items_fine_total = sum(item.get("Fine (24K)", 0) for item in self.items)
        old_balance_rs, old_balance_fine = self.fetch_old_balances()
        reference_rate = self.get_reference_rate()
        exchange_fine_total = sum(row_data.get("fine_24k", 0) for row_data in self.exchange_items if any([
            row_data["product_var"].get().strip(),
            row_data["net_weight_var"].get().strip(),
            row_data["purity_var"].get().strip(),
        ]))
        net_fine_payable = old_balance_fine + items_fine_total
        outstanding_fine_total = items_fine_total
        carry_forward_fine = max(0.0, net_fine_payable - exchange_fine_total)
        exchange_price_equivalent = exchange_fine_total * reference_rate if reference_rate > 0 else 0.0
        total_amount = max(0.0, old_balance_rs + items_total - exchange_price_equivalent)
        return {
            "items_total": items_total,
            "items_fine_total": items_fine_total,
            "exchange_fine_total": exchange_fine_total,
            "exchange_price_equivalent": exchange_price_equivalent,
            "old_balance_fine": old_balance_fine,
            "net_fine_payable": net_fine_payable,
            "outstanding_fine_total": outstanding_fine_total,
            "carry_forward_fine": carry_forward_fine,
            "total_due_fine": net_fine_payable,
            "old_balance": old_balance_rs,
            "grand_total": total_amount,
            "total_amount": total_amount,
            "current_invoice_amount": items_total,
            "reference_rate": reference_rate,
        }

    def create_totals_section(self):
        self.totals_frame = tk.Frame(self.page_content)
        self.totals_frame.pack(fill=tk.X, pady=10)
        self.totals_grid = tk.Frame(self.totals_frame)
        self.totals_grid.pack(fill=tk.X, padx=10)
        labels = [
            ("items_total_label", "Items Total (Incl. GST): 0.00"),
            ("items_fine_label", "Items Fine (24K): 0.000"),
            ("exchange_fine_label", "Exchange Fine (24K): 0.000"),
            ("old_balance_fine_label", "Old Balance Fine (24K): 0.000"),
            ("outstanding_fine_label", "Outstanding Fine (24K): 0.000"),
            ("carry_forward_label", "Carry Forward Fine (24K): 0.000"),
            ("old_balance_total_label", "Old Balance Rs: 0.00"),
            ("total_amount_label", "Total Amount: 0.00"),
        ]
        for index, (attr, text) in enumerate(labels):
            row_index = index // 4
            column_index = index % 4
            label = tk.Label(self.totals_grid, text=text, font=("Arial", 12))
            label.grid(row=row_index, column=column_index, padx=10, pady=4, sticky="w")
            setattr(self, attr, label)
        for column_index in range(4):
            self.totals_grid.grid_columnconfigure(column_index, weight=1)
        self.update_totals()

    def update_totals(self):
        if not hasattr(self, "items_total_label"):
            return
        summary = self.calculate_invoice_summary()
        self.items_total_label.config(text=f"Items Total (Incl. GST): {summary['items_total']:.2f}")
        self.items_fine_label.config(text=f"Items Fine (24K): {summary['items_fine_total']:.3f}")
        self.exchange_fine_label.config(text=f"Exchange Fine (24K): {summary['exchange_fine_total']:.3f}")
        self.old_balance_fine_label.config(text=f"Old Balance Fine (24K): {summary['old_balance_fine']:.3f}")
        self.outstanding_fine_label.config(text=f"Outstanding Fine (24K): {summary['outstanding_fine_total']:.3f}")
        self.carry_forward_label.config(text=f"Carry Forward Fine (24K): {summary['carry_forward_fine']:.3f}")
        self.old_balance_total_label.config(text=f"Old Balance Rs: {summary['old_balance']:.2f}")
        self.total_amount_label.config(text=f"Total Amount: {summary['total_amount']:.2f}")

    def edit_item(self, index):
        if index < 0 or index >= len(self.items):
            return

        def save_callback(updated_item):
            self.items[index] = updated_item
            self.recalculate_items()

        BusinessEditItemPage(
            self.root,
            self.items[index],
            self.gst_percent,
            self.gst_enabled,
            save_callback,
        )

    def refresh_invoice_navigation(self):
        for widget in _iter_all_widgets(self.page_content):
            _bind_mousewheel_to_canvas(widget, self.page_canvas)
        for widget in _iter_focusable_widgets(self.page_content):
            _bind_mousewheel_to_canvas(widget, self.page_canvas)
            _bind_keyboard_navigation(widget, self.page_content, self.page_canvas, self.page_content)

    def cancel_invoice(self):
        messagebox.showinfo("Invoice Canceled", "The invoice has been canceled.")
        self.go_back_callback()

    def make_payment(self):
        summary = self.calculate_invoice_summary()
        has_sale_items = bool(self.items)
        has_exchange_payment = summary.get("exchange_fine_total", 0.0) > 0
        has_old_balance = (
            summary.get("old_balance", 0.0) > 0
            or summary.get("old_balance_fine", 0.0) > 0
        )
        if not (has_sale_items or has_exchange_payment or has_old_balance):
            messagebox.showerror("Error", "Add sale items, exchange payment, or old balance before making payment.")
            return

        payment_window = tk.Toplevel(self.root)
        payment_window.title("Business Invoice Payment")
        payment_window.geometry("420x360")
        payment_window.grab_set()
        payable_fine_total = max(0.0, summary.get("total_due_fine", 0.0))
        exchange_payment_fine = max(0.0, summary.get("exchange_fine_total", 0.0))
        exchange_payment_rs = max(0.0, summary.get("exchange_price_equivalent", 0.0))
        reference_rate = summary.get("reference_rate", 0.0)

        default_mode = "Fine" if exchange_payment_fine > 0 else "Price"
        default_value = (
            f"{exchange_payment_fine:.3f}"
            if default_mode == "Fine"
            else f"{exchange_payment_rs:.2f}"
        )
        payment_mode_var = tk.StringVar(value=default_mode)
        payment_value_var = tk.StringVar(value=default_value)
        conversion_guard = {"active": False}
        remaining_fine_label_var = tk.StringVar()
        remaining_rs_label_var = tk.StringVar()

        tk.Label(payment_window, text="Business Invoice Payment", font=("Arial", 14, "bold")).pack(pady=(10, 8))
        info_frame = tk.Frame(payment_window)
        info_frame.pack(fill=tk.X, padx=15)
        tk.Label(info_frame, text=f"Items Total (Incl. GST): {summary['items_total']:.2f}", anchor="w").pack(fill=tk.X)
        tk.Label(info_frame, text=f"Items Fine (24K): {summary['items_fine_total']:.3f}", anchor="w").pack(fill=tk.X)
        tk.Label(info_frame, text=f"Exchange Fine Paid (24K): {summary['exchange_fine_total']:.3f}", anchor="w").pack(fill=tk.X)
        tk.Label(info_frame, text=f"Exchange Price Paid (Rs): {summary['exchange_price_equivalent']:.2f}", anchor="w").pack(fill=tk.X)
        tk.Label(info_frame, text=f"Old Balance Fine (24K): {summary['old_balance_fine']:.3f}", anchor="w").pack(fill=tk.X)
        tk.Label(info_frame, text=f"Old Balance Rs: {summary['old_balance']:.2f}", anchor="w").pack(fill=tk.X)
        tk.Label(info_frame, text=f"Outstanding Fine (24K): {summary['outstanding_fine_total']:.3f}", anchor="w").pack(fill=tk.X)
        tk.Label(info_frame, textvariable=remaining_fine_label_var, anchor="w").pack(fill=tk.X)
        tk.Label(info_frame, textvariable=remaining_rs_label_var, anchor="w").pack(fill=tk.X)
        tk.Label(info_frame, text=f"Reference Rate (Rs/Gram): {reference_rate:.2f}", anchor="w").pack(fill=tk.X)

        mode_frame = tk.Frame(payment_window)
        mode_frame.pack(pady=(10, 5))
        tk.Label(mode_frame, text="Pay With:", font=("Arial", 11)).pack(side=tk.LEFT, padx=(0, 10))

        def on_mode_changed():
            if conversion_guard["active"]:
                return
            current_mode = payment_mode_var.get()
            conversion_guard["active"] = True
            if current_mode == "Fine":
                payment_value_var.set(f"{exchange_payment_fine:.3f}")
            else:
                converted_amount = exchange_payment_rs
                if converted_amount <= 0 and reference_rate > 0:
                    converted_amount = exchange_payment_fine * reference_rate
                payment_value_var.set(f"{converted_amount:.2f}")
            conversion_guard["active"] = False
            update_conversion()

        def update_remaining_fine_label():
            amount = self.parse_float(payment_value_var.get())
            if payment_mode_var.get() == "Fine":
                paid_fine = amount
                paid_rs = amount * reference_rate if reference_rate > 0 else 0.0
            else:
                paid_rs = amount
                paid_fine = amount / reference_rate if reference_rate > 0 else 0.0

            remaining_fine = max(0.0, summary["net_fine_payable"] - paid_fine)
            remaining_rs = max(0.0, (summary["items_total"] + summary["old_balance"]) - paid_rs)
            remaining_fine_label_var.set(f"Remaining Fine After Payment (24K): {remaining_fine:.3f}")
            remaining_rs_label_var.set(f"Remaining Rs After Payment: {remaining_rs:.2f}")

        tk.Radiobutton(mode_frame, text="Pay In Fine (24K)", variable=payment_mode_var, value="Fine", command=on_mode_changed).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(mode_frame, text="Pay In Price (Rs)", variable=payment_mode_var, value="Price", command=on_mode_changed).pack(side=tk.LEFT, padx=5)

        tk.Label(payment_window, text="Payment Amount:", font=("Arial", 12)).pack(pady=(8, 5))
        payment_entry = tk.Entry(payment_window, width=24, textvariable=payment_value_var, justify="center")
        payment_entry.pack(pady=5)

        helper_label = tk.Label(
            payment_window,
            text="Edit payment if needed. Exchange value is prefilled and remaining fine/rupees are recalculated.",
            font=("Arial", 10),
            wraplength=380,
            justify="center"
        )
        helper_label.pack(pady=(2, 4))

        conversion_label = tk.Label(payment_window, text="", font=("Arial", 10))
        conversion_label.pack(pady=(5, 8))

        def update_conversion(*args):
            if conversion_guard["active"]:
                return
            amount = self.parse_float(payment_value_var.get())
            if reference_rate <= 0:
                conversion_label.config(text="Reference rate not available for conversion.")
                update_remaining_fine_label()
                return

            if payment_mode_var.get() == "Fine":
                conversion_label.config(text=f"Price Equivalent (Rs): {amount * reference_rate:.2f}")
            else:
                conversion_label.config(text=f"Fine Equivalent (24K): {amount / reference_rate:.3f}")
            update_remaining_fine_label()

        payment_value_var.trace_add("write", update_conversion)
        update_conversion()

        def process_payment():
            try:
                exchange_entries = self.get_exchange_entries()
                self.recalculate_items()
                summary = self.calculate_invoice_summary()
                payment_mode = payment_mode_var.get()
                payment_amount = self.parse_float(payment_value_var.get())
                gross_total_amount = summary["items_total"] + summary["old_balance"]
                if payment_mode == "Fine":
                    paid_fine = payment_amount
                    paid_price_equivalent = paid_fine * reference_rate if reference_rate > 0 else 0.0
                else:
                    if reference_rate <= 0:
                        raise ValueError("Reference rate is required to convert fine payment into cash.")
                    paid_price_equivalent = payment_amount
                    paid_fine = paid_price_equivalent / reference_rate

                max_payable_fine = max(0.0, summary["net_fine_payable"])
                paid_fine = min(max_payable_fine, max(0.0, paid_fine))
                paid_price_equivalent = max(0.0, paid_price_equivalent)
                remaining_balance = max(0.0, gross_total_amount - paid_price_equivalent)
                next_carry_forward_fine = max(0.0, summary["net_fine_payable"] - paid_fine)
                conn = self.customer_details["conn"]
                cursor = conn.cursor()
                purchase_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                invoice_date = purchase_timestamp
                updated_at = purchase_timestamp

                inserted_master = False
                for _ in range(25):
                    candidate_invoice_number = _next_invoice_number(conn)
                    try:
                        cursor.execute("""
                            INSERT INTO invoices (
                                customer_code, invoice_number, invoice_date, updated_at,
                                invoice_type, customer_role,
                                items_total, exchange_total, old_balance_included, grand_total,
                                amount_paid, remaining_balance,
                                payment_mode, paid_fine_24k, paid_price_equivalent,
                                business_items_fine, business_exchange_fine,
                                from_last_invoice_fine, carry_forward_fine
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            self.customer_details["customer_code"],
                            candidate_invoice_number,
                            invoice_date,
                            updated_at,
                            "business",
                            "BusinessMan",
                            summary["items_total"],
                            paid_price_equivalent,
                            summary["old_balance"],
                            gross_total_amount,
                            paid_price_equivalent,
                            remaining_balance,
                            payment_mode,
                            paid_fine,
                            paid_price_equivalent,
                            summary["items_fine_total"],
                            paid_fine,
                            summary["old_balance_fine"],
                            next_carry_forward_fine,
                        ))
                        self.invoice_number = candidate_invoice_number
                        inserted_master = True
                        break
                    except sqlite3.IntegrityError as exc:
                        conn.rollback()
                        if "UNIQUE constraint failed: invoices.invoice_number" in str(exc):
                            continue
                        raise

                if not inserted_master:
                    raise ValueError("Unable to reserve a unique invoice number. Please try again.")

                for item in self.items:
                    cursor.execute("""
                        INSERT INTO business_invoice_details (
                            customer_code, invoice_number, invoice_date, customer_name, mobile,
                            product_name, net_weight, gross_weight, purity, wastage_percent,
                            labour, rate_per_gram, fine_24k, total_rate,
                            gst_percent, gst_amount, final_price
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        self.customer_details["customer_code"],
                        self.invoice_number,
                        invoice_date,
                        self.customer_details["name"],
                        self.customer_details["mobile"],
                        item["Product Name"],
                        item["Net Weight"],
                        item["Gross Weight"],
                        item["Purity"],
                        item["Wastage %"],
                        item["Labour"],
                        item["Rate Per Gram"],
                        item["Fine (24K)"],
                        item["Total Rate"],
                        item["GST %"],
                        item["GST Amount"],
                        item["Final Price"],
                    ))

                for row in exchange_entries:
                    cursor.execute("""
                        INSERT INTO business_exchange_details (
                            customer_code, invoice_number, invoice_date,
                            product_name, net_weight, purity, fine_24k
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        self.customer_details["customer_code"],
                        self.invoice_number,
                        invoice_date,
                        row["product_name"],
                        row["net_weight"],
                        row["purity"],
                        row["fine_24k"],
                    ))

                cursor.execute("""
                    UPDATE customers
                    SET balance = ?, fine_balance = ?
                    WHERE customer_code = ?
                """, (
                    remaining_balance,
                    next_carry_forward_fine,
                    self.customer_details["customer_code"]
                ))

                conn.commit()
                CSVUtils.export_master_csv(conn)
                refresh_customer_invoice_files(conn, self.customer_details["customer_code"])
                print_business_invoice_for(conn, self.invoice_number)

                messagebox.showinfo(
                    "Payment Successful",
                    f"Gross Total Amount: {gross_total_amount:.2f}\n"
                    f"Mode: {payment_mode}\n"
                    f"Paid Fine (24K): {paid_fine:.3f}\n"
                    f"Price Equivalent (Rs): {paid_price_equivalent:.2f}\n"
                    f"Remaining Balance: {remaining_balance:.2f}\n"
                    f"Old Balance Fine (Current Invoice): {summary['old_balance_fine']:.3f}\n"
                    f"Carry Forward Fine (Next Invoice): {next_carry_forward_fine:.3f}"
                )
                payment_window.destroy()
                self.go_back_callback()
            except ValueError as exc:
                try:
                    self.customer_details["conn"].rollback()
                except Exception:
                    pass
                messagebox.showerror("Error", str(exc) if str(exc) else "Enter a valid payment amount.")
            except Exception as e:
                try:
                    self.customer_details["conn"].rollback()
                except Exception:
                    pass
                logging.error(f"Business payment processing error: {e}")
                messagebox.showerror("Error", f"Payment failed: {e}")

        tk.Button(payment_window, text="Submit", command=process_payment).pack(pady=10)
        tk.Button(payment_window, text="Cancel", command=payment_window.destroy).pack(pady=5)
        for widget in _iter_focusable_widgets(payment_window):
            _bind_keyboard_navigation(widget, payment_window)


class BusinessAddItemPage:
    def __init__(self, root, gst_percent, gst_enabled, add_item_callback):
        self.root = root
        self.gst_percent = gst_percent
        self.gst_enabled = gst_enabled
        self.add_item_callback = add_item_callback
        self.create_page()

    def create_page(self):
        self.top = tk.Toplevel(self.root)
        self.top.title("Add Business Item")
        tk.Label(self.top, text="Add Business Item", font=("Arial", 16)).pack(pady=15)
        self.product_name_entry = self.create_entry("Product Name")
        self.net_weight_entry = self.create_entry("Net Weight")
        self.gross_weight_entry = self.create_entry("Gross Weight")
        self.purity_entry = self.create_entry("Purity / Tunch")
        self.wastage_entry = self.create_entry("WSTG %")
        self.labour_entry = self.create_entry("Labour")
        self.rate_entry = self.create_entry("Rate Per Gram")
        tk.Button(self.top, text="Add", command=self.add_item).pack(pady=10)
        tk.Button(self.top, text="Cancel", command=self.top.destroy).pack(pady=5)
        self.apply_keyboard_navigation()

    def create_entry(self, label_text):
        frame = tk.Frame(self.top)
        frame.pack(pady=5, padx=20, anchor='w')
        tk.Label(frame, text=label_text, width=20, anchor='w').pack(side=tk.LEFT)
        entry = tk.Entry(frame, width=30)
        entry.pack(side=tk.LEFT, padx=10)
        return entry

    def add_item(self):
        try:
            product_name = self.product_name_entry.get().strip()
            net_weight = float(self.net_weight_entry.get())
            gross_weight = float(self.gross_weight_entry.get() or 0)
            purity = float(self.purity_entry.get())
            wastage = float(self.wastage_entry.get() or 0)
            labour = float(self.labour_entry.get() or 0)
            rate = float(self.rate_entry.get())
            if not product_name:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Enter valid item details.")
            return

        effective_purity = purity + wastage
        fine_24k = net_weight * (effective_purity / 100.0)
        total_rate = (fine_24k * rate) + labour
        gst_amount = total_rate * (self.gst_percent / 100.0) if self.gst_enabled else 0.0
        final_price = total_rate + gst_amount

        self.add_item_callback({
            "Product Name": product_name,
            "Net Weight": net_weight,
            "Gross Weight": gross_weight,
            "Purity": purity,
            "Wastage %": wastage,
            "Labour": labour,
            "Rate Per Gram": rate,
            "Fine (24K)": fine_24k,
            "Total Rate": total_rate,
            "GST %": self.gst_percent if self.gst_enabled else 0.0,
            "GST Amount": gst_amount,
            "Final Price": final_price,
        })
        self.top.destroy()

    def apply_keyboard_navigation(self):
        for widget in _iter_focusable_widgets(self.top):
            _bind_keyboard_navigation(widget, self.top)


class BusinessEditItemPage:
    def __init__(self, root, item_details, gst_percent, gst_enabled, save_callback):
        self.root = root
        self.item_details = item_details
        self.gst_percent = gst_percent
        self.gst_enabled = gst_enabled
        self.save_callback = save_callback
        self.create_page()

    def create_page(self):
        self.top = tk.Toplevel(self.root)
        self.top.title("Edit Business Item")
        tk.Label(self.top, text="Edit Business Item", font=("Arial", 16)).pack(pady=15)
        self.product_name_entry = self.create_entry("Product Name", self.item_details.get("Product Name", ""))
        self.net_weight_entry = self.create_entry("Net Weight", self.item_details.get("Net Weight", 0))
        self.gross_weight_entry = self.create_entry("Gross Weight", self.item_details.get("Gross Weight", 0))
        self.purity_entry = self.create_entry("Purity / Tunch", self.item_details.get("Purity", 0))
        self.wastage_entry = self.create_entry("WSTG %", self.item_details.get("Wastage %", 0))
        self.labour_entry = self.create_entry("Labour", self.item_details.get("Labour", 0))
        self.rate_entry = self.create_entry("Rate Per Gram", self.item_details.get("Rate Per Gram", 0))
        tk.Button(self.top, text="Save", command=self.save_item).pack(pady=10)
        tk.Button(self.top, text="Cancel", command=self.top.destroy).pack(pady=5)
        self.apply_keyboard_navigation()

    def create_entry(self, label_text, initial_value):
        frame = tk.Frame(self.top)
        frame.pack(pady=5, padx=20, anchor="w")
        tk.Label(frame, text=label_text, width=20, anchor="w").pack(side=tk.LEFT)
        entry = tk.Entry(frame, width=30)
        entry.insert(0, initial_value)
        entry.pack(side=tk.LEFT, padx=10)
        return entry

    def save_item(self):
        try:
            product_name = self.product_name_entry.get().strip()
            net_weight = float(self.net_weight_entry.get())
            gross_weight = float(self.gross_weight_entry.get() or 0)
            purity = float(self.purity_entry.get())
            wastage = float(self.wastage_entry.get() or 0)
            labour = float(self.labour_entry.get() or 0)
            rate = float(self.rate_entry.get())
            if not product_name or net_weight <= 0 or purity < 0 or rate < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Enter valid item details.")
            return

        effective_purity = purity + wastage
        fine_24k = net_weight * (effective_purity / 100.0)
        total_rate = (fine_24k * rate) + labour
        gst_amount = total_rate * (self.gst_percent / 100.0) if self.gst_enabled else 0.0
        final_price = total_rate + gst_amount

        self.save_callback({
            "Product Name": product_name,
            "Net Weight": net_weight,
            "Gross Weight": gross_weight,
            "Purity": purity,
            "Wastage %": wastage,
            "Labour": labour,
            "Rate Per Gram": rate,
            "Fine (24K)": fine_24k,
            "Total Rate": total_rate,
            "GST %": self.gst_percent if self.gst_enabled else 0.0,
            "GST Amount": gst_amount,
            "Final Price": final_price,
        })
        self.top.destroy()

    def apply_keyboard_navigation(self):
        for widget in _iter_focusable_widgets(self.top):
            _bind_keyboard_navigation(widget, self.top)


class AddItemPage:
    def __init__(self, root, add_item_callback, go_back_callback):
        self.root = root
        self.add_item_callback = add_item_callback
        self.go_back_callback = go_back_callback
        self.conn = _connect_app_db(timeout=30)
        self.cursor = self.conn.cursor()
        self.cursor.execute("PRAGMA busy_timeout = 30000")
        self.create_add_item_page()

    def create_add_item_page(self):
        self.top = tk.Toplevel(self.root)
        self.top.title("Add Item")
        tk.Label(self.top, text="Add Item", font=("Arial", 16)).pack(pady=20)

        self.cursor.execute("SELECT DISTINCT product_name FROM stock WHERE quantity > 0")
        product_names = [row[0] for row in self.cursor.fetchall()]
        tk.Label(self.top, text="Product Name", width=20, anchor='w').pack()
        self.product_var = tk.StringVar()
        self.product_combo = ttk.Combobox(self.top, textvariable=self.product_var, values=product_names, state="readonly", width=30)
        self.product_combo.pack(pady=5)
        self.product_combo.bind("<<ComboboxSelected>>", self.on_product_selected)

        tk.Label(self.top, text="Select Stock Entry", width=30, anchor='w').pack()
        self.stock_var = tk.StringVar()
        self.stock_combo = ttk.Combobox(self.top, textvariable=self.stock_var, values=[], state="readonly", width=60)
        self.stock_combo.pack(pady=5)
        self.stock_combo.bind("<<ComboboxSelected>>", self.fill_weights_from_stock)

        self.quantity_entry = self.create_entry(self.top, "Quantity")
        self.net_weight_entry = self.create_entry(self.top, "Net Weight (per qty)")
        self.gross_weight_entry = self.create_entry(self.top, "Gross Weight (per qty)")
        self.rate_per_gram_entry = self.create_entry(self.top, "Rate Per Gram")

        self.gold_type = tk.StringVar(value="22c")
        gold_type_frame = tk.Frame(self.top)
        gold_type_frame.pack(pady=5)
        tk.Label(gold_type_frame, text="Gold Type", width=20, anchor='w').pack(side=tk.LEFT)
        tk.Radiobutton(gold_type_frame, text="22c", variable=self.gold_type, value="22c").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(gold_type_frame, text="24c", variable=self.gold_type, value="24c").pack(side=tk.LEFT, padx=5)

        tk.Button(self.top, text="Add", command=self.add_item).pack(pady=10)
        tk.Button(self.top, text="Back", command=self.top.destroy).pack(pady=10)
        self.apply_keyboard_navigation()

    def on_product_selected(self, event=None):
        pname = self.product_var.get()
        self.cursor.execute("SELECT id, quantity, net_weight, gross_weight FROM stock WHERE product_name = ? AND quantity > 0", (pname,))
        stock_rows = self.cursor.fetchall()
        display_options = [
            f"Qty: {row[1]} | Net: {row[2]/row[1] if row[1] else 0} | Gross: {row[3]/row[1] if row[1] else 0} | StockID:{row[0]}"
            for row in stock_rows
        ]
        self.stock_combo['values'] = display_options
        self.stock_var.set("")
        self.quantity_entry.delete(0, tk.END)
        self.net_weight_entry.delete(0, tk.END)
        self.gross_weight_entry.delete(0, tk.END)

    def fill_weights_from_stock(self, event=None):
        selected = self.stock_var.get()
        if not selected:
            return
        try:
            stock_id = int(selected.split("StockID:")[-1])
        except Exception:
            return
        self.cursor.execute("SELECT quantity, net_weight, gross_weight FROM stock WHERE id = ?", (stock_id,))
        row = self.cursor.fetchone()
        if row:
            qty, net_wt, gross_wt = row
            # Calculate per qtyweights 
            per_net_wt = net_wt / qty if qty else 0
            per_gross_wt = gross_wt / qty if qty else 0
            self.quantity_entry.delete(0, tk.END)
            self.quantity_entry.insert(0, "1")
            self.net_weight_entry.delete(0, tk.END)
            self.net_weight_entry.insert(0, str(round(per_net_wt, 3)))
            self.gross_weight_entry.delete(0, tk.END)
            self.gross_weight_entry.insert(0, str(round(per_gross_wt, 3)))
            self.available_qty = qty

    def create_entry(self, parent, label_text):
        frame = tk.Frame(parent)
        frame.pack(pady=5, padx=20, anchor='w')  
        tk.Label(frame, text=label_text, width=20, anchor='w').pack(side=tk.LEFT)  
        entry = tk.Entry(frame, width=30)
        entry.pack(side=tk.LEFT, padx=10)
        return entry

    def add_item(self):
        pname = self.product_var.get()
        selected = self.stock_var.get()
        if not pname or not selected:
            messagebox.showerror("Error", "Please select a product and stock entry.")
            return
        try:
            stock_id = int(selected.split("StockID:")[-1])
        except Exception:
            messagebox.showerror("Error", "Invalid stock selection.")
            return
        try:
            quantity = float(self.quantity_entry.get())
            net_weight = float(self.net_weight_entry.get())
            gross_weight = float(self.gross_weight_entry.get())
            rate_per_gram = float(self.rate_per_gram_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Please enter valid numbers for quantity and weights.")
            return

        self.cursor.execute("SELECT quantity FROM stock WHERE id = ?", (stock_id,))
        stock_row = self.cursor.fetchone()
        if not stock_row:
            messagebox.showerror("Error", "Stock entry not found.")
            return
        stock_qty = stock_row[0]
        if quantity > stock_qty:
            messagebox.showerror("Error", f"Cannot add more than available stock ({stock_qty}).")
            return

        if self.gold_type.get() == "22c":
            self.cursor.execute("SELECT making_charges_22c FROM settings")
            making_charges_percent = self.cursor.fetchone()[0] or 10.0
            making_charges_per_gram = rate_per_gram * (making_charges_percent / 100)
            cat = "22c"
        else:
            
            self.cursor.execute("SELECT making_charges_24c FROM settings LIMIT 1")
            making_charges_per_gram = self.cursor.fetchone()[0] or 100.0
            cat = "24c"


        total_making_charges = quantity * net_weight * making_charges_per_gram
        total_rupees = (net_weight * rate_per_gram * quantity) + total_making_charges

        item_details = {
            "Category": cat,
            "Product Name": pname,
            "Quantity": quantity,
            "Net Weight": net_weight,
            "Gross Weight": gross_weight,
            "Rate Per Gram": rate_per_gram,
            "Making Charges Per Gram": making_charges_per_gram,
            "Total Making Charges": total_making_charges,
            "Total Rupees": total_rupees,
            "Stock ID": stock_id
        }
        self.add_item_callback(item_details)
        self.top.destroy()

    def apply_keyboard_navigation(self):
        for widget in _iter_focusable_widgets(self.top):
            _bind_keyboard_navigation(widget, self.top)

class EditItemPage:
    def __init__(self, root, item_details, save_callback):
        self.root = root
        self.item_details = item_details
        self.save_callback = save_callback
        self.create_edit_item_page()

    def create_edit_item_page(self):
        self.top = tk.Toplevel(self.root)
        self.top.title("Edit Item")
        tk.Label(self.top, text="Edit Item", font=("Arial", 16)).pack(pady=20)

        
        self.product_name_entry = self.create_entry(self.top, "Product Name", self.item_details["Product Name"])
        self.quantity_entry = self.create_entry(self.top, "Quantity", self.item_details["Quantity"])
        self.net_weight_entry = self.create_entry(self.top, "Net Weight", self.item_details["Net Weight"])
        self.gross_weight_entry = self.create_entry(self.top, "Gross Weight", self.item_details["Gross Weight"])
        self.rate_per_gram_entry = self.create_entry(self.top, "Rate Per Gram", self.item_details["Rate Per Gram"])

        
        self.gold_type = tk.StringVar(value="22c" if self.item_details["Making Charges Per Gram"] == 100.0 else "24c")
        gold_type_frame = tk.Frame(self.top)
        gold_type_frame.pack(pady=5)
        tk.Label(gold_type_frame, text="Gold Type", width=20, anchor='w').pack(side=tk.LEFT)
        tk.Radiobutton(gold_type_frame, text="22c", variable=self.gold_type, value="22c").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(gold_type_frame, text="24c", variable=self.gold_type, value="24c").pack(side=tk.LEFT, padx=5)

        tk.Button(self.top, text="Save", command=self.save_item).pack(pady=10)
        tk.Button(self.top, text="Cancel", command=self.top.destroy).pack(pady=10)
        self.apply_keyboard_navigation()

    def create_entry(self, parent, label_text, initial_value):
        frame = tk.Frame(parent)
        frame.pack(pady=5, padx=20, anchor='w')
        tk.Label(frame, text=label_text, width=20, anchor='w').pack(side=tk.LEFT)
        entry = tk.Entry(frame, width=30)
        entry.insert(0, initial_value)
        entry.pack(side=tk.LEFT, padx=10)
        return entry

    def save_item(self):
        try:
            product_name = self.product_name_entry.get().strip()
            quantity = float(self.quantity_entry.get())
            net_weight = float(self.net_weight_entry.get())
            gross_weight = float(self.gross_weight_entry.get())
            rate_per_gram = float(self.rate_per_gram_entry.get())

            # Basic validation
            if quantity <= 0 or net_weight <= 0 or rate_per_gram <= 0:
                messagebox.showerror("Error", "Values must be greater than 0.")
                return

            updated_item = {
                "Product Name": product_name,
                "Quantity": quantity,
                "Net Weight": net_weight,
                "Gross Weight": gross_weight,
                "Rate Per Gram": rate_per_gram,
                "Category": self.gold_type.get()  # ðŸ”¥ CRITICAL FIX
            }

            if "Stock ID" in self.item_details:
                updated_item["Stock ID"] = self.item_details["Stock ID"]

            # Send raw data back to InvoicePage
            self.save_callback(updated_item)

            # Close edit window
            self.top.destroy()

        except ValueError:
            messagebox.showerror("Error", "Invalid input. Please enter valid numbers.")

    def apply_keyboard_navigation(self):
        for widget in _iter_focusable_widgets(self.top):
            _bind_keyboard_navigation(widget, self.top)

class MakePaymentPage:
    def __init__(self, root, conn, go_back_callback):
        self.root = root
        self.conn = conn
        self.cursor = self.conn.cursor()
        self.go_back_callback = go_back_callback
        self.selected_customer_code = None
        self.selected_invoice_number = None
        self.create_payment_page()

    def clear_frame(self):
        
        for widget in self.root.winfo_children():
            widget.destroy()

    def create_payment_page(self):
        self.clear_frame()

        style = ttk.Style(self.root)
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

        self.root.configure(bg="#f4f4f4")

       
        card = ttk.Frame(self.root, padding=30)
        card.place(relx=0.5, rely=0.35, anchor="center")

        ttk.Label(
            card,
            text="Make Payment",
            font=("Segoe UI", 18, "bold")
        ).grid(row=0, column=0, columnspan=2, pady=(0, 20))

       
        self.search_type = tk.StringVar(value="invoice")

        ttk.Radiobutton(
            card,
            text="Search by Invoice Number",
            variable=self.search_type,
            value="invoice"
        ).grid(row=1, column=0, columnspan=2, pady=5)

        ttk.Radiobutton(
            card,
            text="Search by Mobile Number",
            variable=self.search_type,
            value="mobile"
        ).grid(row=2, column=0, columnspan=2, pady=5)

        
        ttk.Label(
            card,
            text="Invoice / Mobile Number",
            anchor="w"
        ).grid(row=3, column=0, sticky="w", pady=8, padx=(0, 10))

        self.search_entry = ttk.Entry(card, width=30)
        self.search_entry.grid(row=3, column=1, pady=8)

        ttk.Button(
            card,
            text="Search",
            command=self.search_customer
        ).grid(row=4, column=0, columnspan=2, pady=10)

        ttk.Button(
            card,
            text="Check Contacts",
            command=self.open_contact_selector
        ).grid(row=5, column=0, columnspan=2, pady=(0, 10))

       
        self.old_balance_label = ttk.Label(
            card,
            text="Old Balance: 0",
            font=("Segoe UI", 12, "bold")
        )
        self.old_balance_label.grid(row=6, column=0, columnspan=2, pady=10)

        #  payment inputs
        ttk.Label(
            card,
            text="Payment Amount",
            anchor="w"
        ).grid(row=7, column=0, sticky="w", pady=8, padx=(0, 10))

        self.payment_entry = ttk.Entry(card, width=30)
        self.payment_entry.grid(row=7, column=1, pady=8)

      
        ttk.Button(
            card,
            text="Submit Payment",
            command=self.process_payment
        ).grid(row=8, column=0, columnspan=2, pady=(15, 5))

        ttk.Button(
            card,
            text="Back",
            command=self.go_back_callback
        ).grid(row=9, column=0, columnspan=2, pady=5)


    def create_entry(self, label_text):
        frame = tk.Frame(self.root)
        frame.pack(pady=5, padx=20, anchor='w')
        tk.Label(frame, text=label_text, width=30, anchor='w').pack(side=tk.LEFT)
        entry = tk.Entry(frame, width=30)
        entry.pack(side=tk.LEFT, padx=10)
        return entry

    def lookup_customer(self, search_type, search_value):
        if search_type == "invoice":
            self.cursor.execute("""
                SELECT i.customer_code,
                       COALESCE(c.mobile, ''),
                       COALESCE(c.balance, 0),
                       i.invoice_number,
                       COALESCE(c.role, 'Customer')
                FROM invoices i
                LEFT JOIN customers c ON c.customer_code = i.customer_code
                WHERE i.invoice_number = ?
                LIMIT 1
            """, (search_value,))
            return self.cursor.fetchone()

        self.cursor.execute("""
            SELECT customer_code,
                   mobile,
                   COALESCE(balance, 0),
                   COALESCE(role, 'Customer')
            FROM customers
            WHERE mobile = ?
            LIMIT 1
        """, (search_value,))
        row = self.cursor.fetchone()
        if not row:
            return None

        self.cursor.execute("""
            SELECT invoice_number
            FROM invoices
            WHERE customer_code = ?
            ORDER BY id DESC
            LIMIT 1
        """, (row[0],))
        invoice_row = self.cursor.fetchone()
        return (row[0], row[1], row[2], invoice_row[0] if invoice_row else None, row[3])

    def apply_customer_selection(self, customer_code, mobile, old_balance, invoice_number=None):
        self.selected_customer_code = customer_code
        self.selected_invoice_number = invoice_number
        self.search_entry.delete(0, tk.END)
        self.search_entry.insert(0, mobile or "")
        self.search_type.set("mobile")
        self.old_balance_label.config(text=f"Old Balance: {float(old_balance):.2f}")

    def open_contact_selector(self):
        try:
            self.cursor.execute("""
                SELECT name, mobile, COALESCE(balance, 0), customer_code
                FROM customers
                WHERE is_active = 1
                  AND COALESCE(role, 'Customer') <> 'BusinessMan'
                  AND COALESCE(balance, 0) <> 0
                ORDER BY LOWER(name), LOWER(mobile)
            """)
            customers = self.cursor.fetchall()
        except sqlite3.Error as e:
            messagebox.showerror("Error", f"Database error: {e}")
            return

        if not customers:
            messagebox.showinfo("Info", "No customers with remaining balance found.")
            return

        popup = tk.Toplevel(self.root)
        popup.title("Check Contacts")
        popup.geometry("560x340")
        popup.grab_set()

        ttk.Label(popup, text="Select Customer", font=("Segoe UI", 12, "bold")).pack(pady=(15, 10))

        table_frame = ttk.Frame(popup, padding=(12, 0, 12, 0))
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("name", "remaining_balance")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        tree.heading("name", text="Name")
        tree.heading("remaining_balance", text="Remaining Balance")
        tree.column("name", width=310, anchor="w")
        tree.column("remaining_balance", width=180, anchor="e")

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        for index, (name, mobile, balance, customer_code) in enumerate(customers):
            tree.insert(
                "",
                "end",
                iid=str(index),
                values=(name, f"{float(balance):.2f}")
            )

        def use_selected_contact():
            selected = tree.selection()
            if not selected:
                messagebox.showerror("Error", "Select a customer first.")
                return
            selected_index = int(selected[0])
            name, mobile, balance, customer_code = customers[selected_index]
            self.apply_customer_selection(customer_code, mobile, balance)
            popup.destroy()

        tree.bind("<Double-1>", lambda _event: use_selected_contact())
        ttk.Button(popup, text="Use Contact", command=use_selected_contact).pack(pady=(12, 6))
        ttk.Button(popup, text="Cancel", command=popup.destroy).pack()

    def search_customer(self):
        search_value = self.search_entry.get().strip()
        search_type = self.search_type.get()

        if not search_value:
            messagebox.showerror("Error", "Please enter a value to search.")
            return

        try:
            result = self.lookup_customer(search_type, search_value)
            if not result:
                messagebox.showerror("Error", "No record found for the given search value.")
                return

            customer_code, mobile, old_balance, invoice_number, role = result
            if _normalize_customer_role(role) == "BusinessMan":
                messagebox.showinfo("Info", "Use Business Invoice Payment from the BusinessMan invoice page.")
                return
            self.selected_customer_code = customer_code
            self.selected_invoice_number = invoice_number
            if search_type == "invoice":
                self.search_entry.delete(0, tk.END)
                self.search_entry.insert(0, search_value)
            else:
                self.search_entry.delete(0, tk.END)
                self.search_entry.insert(0, mobile or "")
            self.old_balance_label.config(text=f"Old Balance: {float(old_balance):.2f}")
            if float(old_balance) == 0:
                messagebox.showinfo("Info", "The customer has already cleared all balances.")
        except sqlite3.Error as e:
            messagebox.showerror("Error", f"Database error: {e}")

    
    def process_payment(self):
        payment_amount = self.payment_entry.get()
        old_balance_text = self.old_balance_label.cget("text")

        if not payment_amount or "Old Balance: " not in old_balance_text:
            messagebox.showerror(
                "Error",
                "Please search for a customer and enter a payment amount."
            )
            return

        try:
            payment_amount = float(payment_amount)
            old_balance = float(old_balance_text.split(": ")[1])
            remaining_balance = old_balance - payment_amount
            updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if remaining_balance < 0:
                remaining_balance = 0

            search_type = self.search_type.get()
            search_value = self.search_entry.get()

            invoice_number = None
            cursor = self.conn.cursor()
            cust_code = self.selected_customer_code

            if search_type == "invoice":
                cursor.execute(
                    "SELECT customer_code FROM invoices WHERE invoice_number = ?",
                    (search_value,)
                )
                res = cursor.fetchone()

                if not res:
                    messagebox.showerror("Error", "Invoice not found.")
                    return

                cust_code = res[0]

                cursor.execute("""
                    UPDATE invoice_details
                    SET remaining_balance = ?, 
                        amount_paid = amount_paid + ?
                    WHERE customer_code = ?
                """, (remaining_balance, payment_amount, cust_code))

                cursor.execute("""
                    UPDATE customers
                    SET balance = ?
                    WHERE customer_code = ?
                """, (remaining_balance, cust_code))

                cursor.execute("""
                    UPDATE invoices
                    SET remaining_balance = ?,
                        amount_paid = amount_paid + ?,
                        updated_at = ?
                    WHERE customer_code = ?
                """, (remaining_balance, payment_amount, updated_at, cust_code))

                invoice_number = search_value

            else:
                if not cust_code:
                    cursor.execute(
                        "SELECT customer_code FROM customers WHERE mobile = ?",
                        (search_value,)
                    )
                    res = cursor.fetchone()
                    cust_code = res[0] if res else None

                if not cust_code:
                    messagebox.showerror("Error", "Customer not found.")
                    return

                cursor.execute("""
                    UPDATE invoice_details
                    SET remaining_balance = ?, 
                        amount_paid = amount_paid + ?
                    WHERE customer_code = ?
                """, (remaining_balance, payment_amount, cust_code))

                cursor.execute("""
                    UPDATE customers
                    SET balance = ?
                    WHERE customer_code = ?
                """, (remaining_balance, cust_code))

                cursor.execute("""
                    UPDATE invoices
                    SET remaining_balance = ?,
                        amount_paid = amount_paid + ?,
                        updated_at = ?
                    WHERE customer_code = ?
                """, (remaining_balance, payment_amount, updated_at, cust_code))

                cursor.execute("""
                    SELECT invoice_number
                    FROM invoices
                    WHERE customer_code = ?
                    ORDER BY id DESC LIMIT 1
                """, (cust_code,))
                r2 = cursor.fetchone()
                invoice_number = r2[0] if r2 else None

          
            self.conn.commit()

         
            try:
                
                CSVUtils.export_master_csv(self.conn)
            except Exception as e:
                logging.error(f"CSV export error: {e}")

            try:
                refresh_customer_invoice_files(self.conn, cust_code)
            except Exception as e:
                logging.error(f"Invoice text refresh error: {e}")

           
            if invoice_number:
                try:
                    print_invoice_for(self.conn, invoice_number)
                except Exception as e:
                    logging.error(f"Auto-print failed: {e}")

            messagebox.showinfo(
                "Success",
                f"Payment successful! Remaining balance: {remaining_balance}"
            )

            self.go_back_callback()

        except ValueError:
            messagebox.showerror(
                "Error",
                "Invalid payment amount. Please enter a valid number."
            )
        except sqlite3.Error as e:
            messagebox.showerror(
                "Error",
                f"Database error: {e}"
            )


class AdminLoginPage:
    def __init__(self, root, conn, go_back_callback):
        self.root = root
        self.conn = conn  
        self.cursor = self.conn.cursor()  
        self.go_back_callback = go_back_callback
        self.create_admin_login_page()

    
    def create_admin_login_page(self):
        try:
            for widget in self.root.winfo_children():
                widget.destroy()

            style = ttk.Style(self.root)
            try:
                if "clam" in style.theme_names():
                    style.theme_use("clam")
            except Exception:
                pass

            self.root.configure(bg="#f0f0f0")

            header = ttk.Label(self.root, text="Admin Dashboard",
                            font=("Segoe UI", 18, "bold"))
            header.pack(pady=18)

            wrap = ttk.Frame(self.root, padding=12)
            wrap.pack(fill=tk.BOTH, expand=True)

            # -------- Fetch Settings (WITH GST) --------
            try:
                self.cursor.execute("""
                    SELECT shop_name, owner_name,
                        COALESCE(shop_contact, ''),
                        COALESCE(shop_address, ''),
                        COALESCE(shop_gst_no, ''),
                        making_charges_22c, making_charges_24c,
                        gst_percent, gst_enabled
                    FROM settings LIMIT 1
                """)
                s = self.cursor.fetchone() or ("", "", "", "", "", 10.0, 100.0, 3.0, 1)
            except Exception:
                s = ("", "", "", "", "", 10.0, 100.0, 3.0, 1)

            settings_frame = ttk.Labelframe(wrap, text="Shop Settings", padding=12)
            settings_frame.pack(fill=tk.X, pady=(0, 10))

            # Shop Name
            ttk.Label(settings_frame, text="Shop Name:", width=25,
                    anchor="w").grid(row=0, column=0, padx=6, pady=4)
            self.admin_shop_entry = ttk.Entry(settings_frame, width=40)
            self.admin_shop_entry.grid(row=0, column=1, padx=6, pady=4)
            self.admin_shop_entry.insert(0, s[0])

            # Owner Name
            ttk.Label(settings_frame, text="Owner Name:", width=25,
                    anchor="w").grid(row=1, column=0, padx=6, pady=4)
            self.admin_owner_entry = ttk.Entry(settings_frame, width=40)
            self.admin_owner_entry.grid(row=1, column=1, padx=6, pady=4)
            self.admin_owner_entry.insert(0, s[1])

            ttk.Label(settings_frame, text="Shop Contact No.:", width=25,
                    anchor="w").grid(row=2, column=0, padx=6, pady=4)
            self.admin_shop_contact_entry = ttk.Entry(settings_frame, width=40)
            self.admin_shop_contact_entry.grid(row=2, column=1, padx=6, pady=4)
            self.admin_shop_contact_entry.insert(0, s[2])

            ttk.Label(settings_frame, text="Shop Address:", width=25,
                    anchor="w").grid(row=3, column=0, padx=6, pady=4)
            self.admin_shop_address_entry = ttk.Entry(settings_frame, width=40)
            self.admin_shop_address_entry.grid(row=3, column=1, padx=6, pady=4)
            self.admin_shop_address_entry.insert(0, s[3])

            ttk.Label(settings_frame, text="Shop GST No.:", width=25,
                    anchor="w").grid(row=4, column=0, padx=6, pady=4)
            self.admin_shop_gst_entry = ttk.Entry(settings_frame, width=40)
            self.admin_shop_gst_entry.grid(row=4, column=1, padx=6, pady=4)
            self.admin_shop_gst_entry.insert(0, s[4])

            # Making Charges 22c
            ttk.Label(settings_frame, text="Making Charges (22c %):",
                    width=25, anchor="w").grid(row=5, column=0, padx=6, pady=4)
            self.admin_m22_entry = ttk.Entry(settings_frame, width=20)
            self.admin_m22_entry.grid(row=5, column=1, padx=6, pady=4, sticky="w")
            self.admin_m22_entry.insert(0, str(s[5]))

            # Making Charges 24c
            ttk.Label(settings_frame, text="Making Charges (24c Rs):",
                    width=25, anchor="w").grid(row=6, column=0, padx=6, pady=4)
            self.admin_m24_entry = ttk.Entry(settings_frame, width=20)
            self.admin_m24_entry.grid(row=6, column=1, padx=6, pady=4, sticky="w")
            self.admin_m24_entry.insert(0, str(s[6]))

            # GST Percent
            ttk.Label(settings_frame, text="GST (%):",
                    width=25, anchor="w").grid(row=7, column=0, padx=6, pady=4)
            self.admin_gst_entry = ttk.Entry(settings_frame, width=20)
            self.admin_gst_entry.grid(row=7, column=1, padx=6, pady=4, sticky="w")
            self.admin_gst_entry.insert(0, str(s[7]))

            # GST Enable Toggle
            self.gst_enabled_var = tk.IntVar(value=s[8])
            ttk.Checkbutton(
                settings_frame,
                text="Enable GST",
                variable=self.gst_enabled_var
            ).grid(row=8, column=1, sticky="w", padx=6, pady=4)

            # Save Button
            ttk.Button(
                settings_frame,
                text="Save Settings",
                command=self.save_admin_settings
            ).grid(row=9, column=1, padx=6, pady=10, sticky="e")

            # -------- Tools Section --------
            tools_frame = ttk.Labelframe(wrap, text="Admin Tools", padding=12)
            tools_frame.pack(fill=tk.X, pady=(0, 10))

            ttk.Button(tools_frame, text="Change Admin PIN",
                    command=lambda: self.change_pin("admin"))\
                .grid(row=0, column=0, padx=8, pady=8, sticky="ew")

            ttk.Button(tools_frame, text="Change Software PIN",
                    command=lambda: self.change_pin("software"))\
                .grid(row=0, column=1, padx=8, pady=8, sticky="ew")

            ttk.Button(tools_frame, text="Manage Stock",
                    command=self.manage_stock)\
                .grid(row=0, column=2, padx=8, pady=8, sticky="ew")

            ttk.Button(tools_frame, text="Show Stock",
                    command=self.show_stock)\
                .grid(row=0, column=3, padx=8, pady=8, sticky="ew")

            ttk.Button(tools_frame, text="Customers List",
                    command=self.customers_list)\
                .grid(row=1, column=0, padx=8, pady=8, sticky="ew")

            ttk.Button(tools_frame, text="Update Old Balance",
                    command=self.open_old_balance_manager)\
                .grid(row=1, column=1, padx=8, pady=8, sticky="ew")
            
            ttk.Button(tools_frame, text="GST Reports",
                    command=self.open_gst_reports_page).grid(row=1, column=2, padx=8, pady=8, sticky="ew")

            ttk.Button(tools_frame, text="Invoice Reports",
                    command=self.open_invoice_reports_page).grid(row=1, column=3, padx=8, pady=8, sticky="ew")

            ttk.Button(tools_frame, text="User History",
                    command=self.open_user_history_page).grid(row=2, column=0, padx=8, pady=8, sticky="ew")

            for i in range(4):
                tools_frame.grid_columnconfigure(i, weight=1)

            bottom = ttk.Frame(wrap)
            bottom.pack(fill=tk.X, pady=(6, 0))
            ttk.Button(bottom, text="Back",
                    command=self.go_back_callback)\
                .pack(side=tk.RIGHT, padx=10, pady=6)

        except Exception as e:
            logging.error(f"Admin dashboard error: {e}")
            messagebox.showerror("Error", f"Failed to create admin dashboard: {e}")

    def save_admin_settings(self):
        try:
            shop = self.admin_shop_entry.get().strip()
            owner = self.admin_owner_entry.get().strip()
            shop_contact = self.admin_shop_contact_entry.get().strip()
            shop_address = self.admin_shop_address_entry.get().strip()
            shop_gst_no = self.admin_shop_gst_entry.get().strip()
            m22 = float(self.admin_m22_entry.get())
            m24 = float(self.admin_m24_entry.get())
            gst_percent = float(self.admin_gst_entry.get())
            gst_enabled = self.gst_enabled_var.get()

        except ValueError:
            messagebox.showerror("Error", "Enter valid numeric values.")
            return

        self.cursor.execute("SELECT id FROM settings LIMIT 1")
        row = self.cursor.fetchone()

        if row:
            self.cursor.execute("""
                UPDATE settings
                SET shop_name=?,
                    owner_name=?,
                    shop_contact=?,
                    shop_address=?,
                    shop_gst_no=?,
                    making_charges_22c=?,
                    making_charges_24c=?,
                    gst_percent=?,
                    gst_enabled=?
                WHERE id=?
            """, (shop, owner, shop_contact, shop_address, shop_gst_no, m22, m24, gst_percent, gst_enabled, row[0]))
        else:
            self.cursor.execute("""
                INSERT INTO settings
                (shop_name, owner_name, shop_contact, shop_address, shop_gst_no,
                making_charges_22c, making_charges_24c,
                gst_percent, gst_enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (shop, owner, shop_contact, shop_address, shop_gst_no, m22, m24, gst_percent, gst_enabled))

        self.conn.commit()
        messagebox.showinfo("Success", "Settings saved successfully.")

    def open_old_balance_manager(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        ttk.Label(
            self.root,
            text="Update Old Balance",
            font=("Segoe UI", 18, "bold")
        ).pack(pady=15)

        action_frame = ttk.Frame(self.root, padding=10)
        action_frame.pack(fill=tk.X)

        ttk.Button(
            action_frame,
            text="Back",
            command=self.create_admin_login_page
        ).pack(side=tk.RIGHT)

        content = ttk.Frame(self.root, padding=10)
        content.pack(fill=tk.BOTH, expand=True)

        search_frame = ttk.Labelframe(content, text="Search Customer", padding=12)
        search_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(search_frame, text="Mobile / Customer Code", width=22, anchor="w").grid(
            row=0, column=0, padx=6, pady=6, sticky="w"
        )
        search_entry = ttk.Entry(search_frame, width=30)
        search_entry.grid(row=0, column=1, padx=6, pady=6, sticky="w")

        details_frame = ttk.Labelframe(content, text="Customer Old Balances", padding=12)
        details_frame.pack(fill=tk.X, pady=(0, 10))

        name_var = tk.StringVar(value="")
        role_var = tk.StringVar(value="")
        customer_code_var = tk.StringVar(value="")
        balance_var = tk.StringVar(value="0.00")
        fine_balance_var = tk.StringVar(value="0.000")
        selected_customer = {"code": None}

        ttk.Label(details_frame, text="Name", width=18, anchor="w").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        ttk.Label(details_frame, textvariable=name_var).grid(row=0, column=1, padx=6, pady=6, sticky="w")

        ttk.Label(details_frame, text="Role", width=18, anchor="w").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        ttk.Label(details_frame, textvariable=role_var).grid(row=1, column=1, padx=6, pady=6, sticky="w")

        ttk.Label(details_frame, text="Customer Code", width=18, anchor="w").grid(row=2, column=0, padx=6, pady=6, sticky="w")
        ttk.Label(details_frame, textvariable=customer_code_var).grid(row=2, column=1, padx=6, pady=6, sticky="w")

        ttk.Label(details_frame, text="Old Balance Rs", width=18, anchor="w").grid(row=3, column=0, padx=6, pady=6, sticky="w")
        ttk.Entry(details_frame, width=20, textvariable=balance_var).grid(row=3, column=1, padx=6, pady=6, sticky="w")

        ttk.Label(details_frame, text="Old Fine (24K)", width=18, anchor="w").grid(row=4, column=0, padx=6, pady=6, sticky="w")
        fine_balance_entry = ttk.Entry(details_frame, width=20, textvariable=fine_balance_var, state="disabled")
        fine_balance_entry.grid(row=4, column=1, padx=6, pady=6, sticky="w")

        ttk.Label(
            details_frame,
            text="Fine can be edited only for Businessman accounts. Customer accounts always keep fine at 0.",
            wraplength=620,
            justify="left"
        ).grid(row=5, column=0, columnspan=2, padx=6, pady=(8, 4), sticky="w")

        table_frame = ttk.Frame(content)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("customer_code", "name", "mobile", "role", "old_balance", "old_fine")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=14)
        for column, heading, width in (
            ("customer_code", "Customer Code", 130),
            ("name", "Name", 220),
            ("mobile", "Mobile", 140),
            ("role", "Role", 110),
            ("old_balance", "Old Balance Rs", 120),
            ("old_fine", "Old Fine 24K", 120),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        def load_customers(search_text=""):
            for row in tree.get_children():
                tree.delete(row)

            params = []
            query = """
                SELECT customer_code,
                       name,
                       mobile,
                       COALESCE(role, 'Customer'),
                       COALESCE(balance, 0),
                       COALESCE(fine_balance, 0)
                FROM customers
            """
            search_text = search_text.strip()
            if search_text:
                query += """
                    WHERE mobile LIKE ?
                       OR customer_code LIKE ?
                       OR name LIKE ?
                """
                wildcard = f"%{search_text}%"
                params.extend([wildcard, wildcard, wildcard])
            query += " ORDER BY id DESC"

            self.cursor.execute(query, params)
            for row in self.cursor.fetchall():
                tree.insert(
                    "",
                    "end",
                    values=(
                        row[0],
                        row[1],
                        row[2],
                        _display_customer_role(row[3]),
                        f"{float(row[4] or 0):.2f}",
                        f"{float(row[5] or 0):.3f}",
                    )
                )

        def apply_customer_row(values):
            customer_code, name, mobile, role, balance, fine_balance = values
            selected_customer["code"] = customer_code
            search_entry.delete(0, tk.END)
            search_entry.insert(0, mobile)
            name_var.set(name)
            normalized_role = _normalize_customer_role(role)
            role_var.set(_display_customer_role(normalized_role))
            customer_code_var.set(customer_code)
            balance_var.set(f"{float(balance):.2f}")
            if normalized_role == "BusinessMan":
                fine_balance_var.set(f"{float(fine_balance):.3f}")
                fine_balance_entry.config(state="normal")
            else:
                fine_balance_var.set("0.000")
                fine_balance_entry.config(state="disabled")

        def use_selected_customer():
            selected = tree.selection()
            if not selected:
                messagebox.showerror("Error", "Please select a customer.")
                return
            apply_customer_row(tree.item(selected[0], "values"))

        def search_customer():
            load_customers(search_entry.get().strip())
            rows = tree.get_children()
            if len(rows) == 1:
                apply_customer_row(tree.item(rows[0], "values"))
            elif not rows:
                messagebox.showerror("Error", "No customer found.")

        def save_old_balances():
            customer_code = selected_customer["code"]
            if not customer_code:
                messagebox.showerror("Error", "Please select a customer first.")
                return

            try:
                old_balance = round(max(0.0, float(balance_var.get().strip() or 0)), 2)
                old_fine = 0.0
                if _normalize_customer_role(role_var.get()) == "BusinessMan":
                    old_fine = round(max(0.0, float(fine_balance_var.get().strip() or 0)), 3)
            except ValueError:
                messagebox.showerror("Error", "Enter valid numeric values for balances.")
                return

            self.cursor.execute(
                """
                UPDATE customers
                SET balance = ?, fine_balance = ?
                WHERE customer_code = ?
                """,
                (old_balance, old_fine, customer_code)
            )
            self.conn.commit()
            balance_var.set(f"{old_balance:.2f}")
            fine_balance_var.set(f"{old_fine:.3f}")
            load_customers(search_entry.get().strip())
            messagebox.showinfo("Success", "Old balances updated successfully.")

        ttk.Button(search_frame, text="Search", command=search_customer).grid(
            row=0, column=2, padx=6, pady=6
        )
        ttk.Button(search_frame, text="Show All", command=lambda: load_customers("")).grid(
            row=0, column=3, padx=6, pady=6
        )

        button_frame = ttk.Frame(content)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(button_frame, text="Use Selected Customer", command=use_selected_customer).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(button_frame, text="Save Old Balances", command=save_old_balances).pack(side=tk.LEFT)

        tree.bind("<Double-1>", lambda _event: use_selected_customer())
        load_customers()

    def open_user_history_page(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        header = ttk.Label(self.root, text="User History", font=("Segoe UI", 18, "bold"))
        header.pack(pady=15)

        self.user_history_filters = {
            "customer_code": None,
            "search_text": tk.StringVar(value=""),
            "invoice_type": tk.StringVar(value="All"),
        }
        self.user_history_selected = None
        self.user_history_selected_user = None
        self.user_history_row_map = {}

        action_frame = ttk.Frame(self.root, padding=10)
        action_frame.pack(fill=tk.X)

        ttk.Button(action_frame, text="Check Contacts", command=self.open_user_history_selector).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(action_frame, text="Back", command=self.create_admin_login_page).pack(side=tk.RIGHT)

        filter_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        filter_frame.pack(fill=tk.X)
        ttk.Label(filter_frame, text="Search").pack(side=tk.LEFT, padx=(0, 6))
        search_entry = ttk.Entry(filter_frame, textvariable=self.user_history_filters["search_text"], width=28)
        search_entry.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(filter_frame, text="Type").pack(side=tk.LEFT, padx=(0, 6))
        type_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.user_history_filters["invoice_type"],
            values=("All", "Retail", "Business"),
            state="readonly",
            width=12
        )
        type_combo.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(filter_frame, text="Apply", command=self.load_user_history).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(filter_frame, text="Show All", command=self.reset_user_history_filters).pack(side=tk.LEFT)

        content = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        list_frame = ttk.Frame(content, padding=8)
        detail_outer = ttk.Frame(content, padding=8)
        content.add(list_frame, weight=2)
        content.add(detail_outer, weight=3)

        columns = ("customer_name", "customer_code", "mobile", "invoice_count", "last_activity")
        self.user_history_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=18)
        headings = {
            "customer_name": ("Name", 170, "w"),
            "customer_code": ("Customer Code", 130, "center"),
            "mobile": ("Mobile", 130, "center"),
            "invoice_count": ("Invoices", 80, "center"),
            "last_activity": ("Last Invoice", 150, "center"),
        }
        for column, (label, width, anchor) in headings.items():
            self.user_history_tree.heading(column, text=label)
            self.user_history_tree.column(column, width=width, anchor=anchor)

        list_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.user_history_tree.yview)
        self.user_history_tree.configure(yscrollcommand=list_scroll.set)
        self.user_history_tree.grid(row=0, column=0, sticky="nsew")
        list_scroll.grid(row=0, column=1, sticky="ns")
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        detail_actions = ttk.Frame(detail_outer)
        detail_actions.pack(fill=tk.X, pady=(0, 8))
        self.user_history_title_var = tk.StringVar(value="Select a user to view all invoices")
        ttk.Label(detail_actions, textvariable=self.user_history_title_var, font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(detail_actions, text="Refresh User", command=self.refresh_selected_user_history).pack(side=tk.RIGHT)

        self.user_history_detail_canvas = tk.Canvas(detail_outer, highlightthickness=0)
        detail_scroll = ttk.Scrollbar(detail_outer, orient=tk.VERTICAL, command=self.user_history_detail_canvas.yview)
        self.user_history_detail_canvas.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.user_history_detail_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.user_history_detail_content = ttk.Frame(self.user_history_detail_canvas)
        self.user_history_detail_window = self.user_history_detail_canvas.create_window(
            (0, 0), window=self.user_history_detail_content, anchor="nw"
        )

        def _sync_history_detail(_event=None):
            self.user_history_detail_canvas.configure(scrollregion=self.user_history_detail_canvas.bbox("all"))

        def _resize_history_detail(event):
            self.user_history_detail_canvas.itemconfigure(self.user_history_detail_window, width=event.width)
            self.user_history_detail_canvas.configure(scrollregion=self.user_history_detail_canvas.bbox("all"))

        self.user_history_detail_content.bind("<Configure>", _sync_history_detail)
        self.user_history_detail_canvas.bind("<Configure>", _resize_history_detail)

        self.user_history_tree.bind("<<TreeviewSelect>>", self.on_user_history_selected)
        search_entry.bind("<Return>", lambda _event: self.load_user_history())
        type_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_user_history())

        self.load_user_history()
        self.render_user_history_empty_state("Select a user to view all invoice details.")

    def reset_user_history_filters(self):
        if not hasattr(self, "user_history_filters"):
            return
        self.user_history_filters["customer_code"] = None
        self.user_history_filters["search_text"].set("")
        self.user_history_filters["invoice_type"].set("All")
        self.load_user_history()

    def load_user_history(self, customer_code=None):
        if not hasattr(self, "user_history_tree"):
            return

        if customer_code is not None and hasattr(self, "user_history_filters"):
            self.user_history_filters["customer_code"] = customer_code

        for row in self.user_history_tree.get_children():
            self.user_history_tree.delete(row)

        self.user_history_row_map = {}
        params = []
        query = """
            SELECT COALESCE(c.name, ''),
                   i.customer_code,
                   COALESCE(c.mobile, ''),
                   COUNT(i.invoice_number),
                   MAX(COALESCE(i.updated_at, i.invoice_date)),
                   COALESCE(MAX(i.customer_role), 'Customer')
            FROM invoices i
            LEFT JOIN customers c ON c.customer_code = i.customer_code
            WHERE 1 = 1
        """

        selected_customer_code = self.user_history_filters["customer_code"] if hasattr(self, "user_history_filters") else None
        if selected_customer_code:
            query += " AND i.customer_code = ?"
            params.append(selected_customer_code)

        search_text = self.user_history_filters["search_text"].get().strip() if hasattr(self, "user_history_filters") else ""
        if search_text:
            wildcard = f"%{search_text}%"
            query += """
                AND (
                    c.name LIKE ?
                    OR c.mobile LIKE ?
                    OR i.customer_code LIKE ?
                )
            """
            params.extend([wildcard, wildcard, wildcard])

        invoice_type = self.user_history_filters["invoice_type"].get() if hasattr(self, "user_history_filters") else "All"
        if invoice_type == "Retail":
            query += " AND COALESCE(i.invoice_type, 'retail') = 'retail'"
        elif invoice_type == "Business":
            query += " AND COALESCE(i.invoice_type, 'retail') = 'business'"

        query += """
            GROUP BY i.customer_code, c.name, c.mobile
            ORDER BY LOWER(COALESCE(c.name, '')), i.customer_code
        """

        self.cursor.execute(query, params)
        rows = self.cursor.fetchall()
        for index, row in enumerate(rows):
            name, customer_code_value, mobile, invoice_count, last_activity, customer_role = row
            item_id = str(index)
            self.user_history_row_map[item_id] = {
                "customer_name": name,
                "customer_code": customer_code_value,
                "mobile": mobile,
                "invoice_count": int(invoice_count or 0),
                "last_activity": last_activity or "",
                "customer_role": customer_role,
            }
            self.user_history_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    name,
                    customer_code_value,
                    mobile,
                    int(invoice_count or 0),
                    _format_history_datetime(last_activity),
                )
            )

        self.user_history_selected = None
        self.user_history_selected_user = None
        if rows:
            first_item = self.user_history_tree.get_children()[0]
            self.user_history_tree.selection_set(first_item)
            self.user_history_tree.focus(first_item)
            self.on_user_history_selected()
        else:
            self.render_user_history_empty_state("No invoice history found for the current filters.")

    def on_user_history_selected(self, _event=None):
        selected = self.user_history_tree.selection()
        if not selected:
            self.user_history_selected = None
            self.user_history_selected_user = None
            self.render_user_history_empty_state("Select a user to view all invoice details.")
            return

        row_data = self.user_history_row_map.get(selected[0])
        if not row_data:
            self.render_user_history_empty_state("Unable to load the selected user.")
            return

        self.user_history_selected = row_data
        self.user_history_selected_user = row_data
        self.render_user_history_for_user(row_data)

    def render_user_history_empty_state(self, message):
        self.user_history_title_var.set("Invoice Details")
        for widget in self.user_history_detail_content.winfo_children():
            widget.destroy()
        ttk.Label(
            self.user_history_detail_content,
            text=message,
            font=("Segoe UI", 11),
            anchor="center",
            justify="center"
        ).pack(fill=tk.BOTH, expand=True, pady=40)

    def render_user_history_for_user(self, user_row_data):
        customer_code = user_row_data.get("customer_code")
        if not customer_code:
            self.render_user_history_empty_state("Unable to load selected user.")
            return

        for widget in self.user_history_detail_content.winfo_children():
            widget.destroy()

        invoice_type_filter = self.user_history_filters["invoice_type"].get() if hasattr(self, "user_history_filters") else "All"
        query = """
            SELECT invoice_number,
                   COALESCE(invoice_date, ''),
                   COALESCE(invoice_type, 'retail'),
                   COALESCE(amount_paid, 0),
                   COALESCE(remaining_balance, 0),
                   COALESCE(customer_role, 'Customer')
            FROM invoices
            WHERE customer_code = ?
        """
        params = [customer_code]
        if invoice_type_filter == "Retail":
            query += " AND COALESCE(invoice_type, 'retail') = 'retail'"
        elif invoice_type_filter == "Business":
            query += " AND COALESCE(invoice_type, 'retail') = 'business'"
        query += " ORDER BY invoice_date DESC, invoice_number DESC"

        self.cursor.execute(query, params)
        invoice_rows = self.cursor.fetchall()
        if not invoice_rows:
            self.render_user_history_empty_state("No invoices found for this user.")
            return

        customer_name = user_row_data.get("customer_name", "")
        self.user_history_title_var.set(
            f"{customer_name} ({customer_code}) | {len(invoice_rows)} invoice(s)"
        )

        for index, invoice_row in enumerate(invoice_rows, start=1):
            invoice_number, invoice_date, invoice_type, amount_paid, remaining_balance, customer_role = invoice_row
            invoice_label = (
                f"{index}. Invoice {invoice_number} | "
                f"{'Business' if invoice_type == 'business' else 'Retail'} | "
                f"{_format_history_datetime(invoice_date)}"
            )
            invoice_block = ttk.LabelFrame(self.user_history_detail_content, text=invoice_label, padding=8)
            invoice_block.pack(fill=tk.X, expand=True, pady=(0, 10))

            top_row = ttk.Frame(invoice_block)
            top_row.pack(fill=tk.X, pady=(0, 6))
            ttk.Label(
                top_row,
                text=f"Role: {_display_customer_role(customer_role)} | Paid: {float(amount_paid or 0):.2f} | Remaining: {float(remaining_balance or 0):.2f}",
                anchor="w"
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(
                top_row,
                text="Print This Invoice",
                command=lambda inv=invoice_number, inv_type=invoice_type: self.print_history_invoice(inv, inv_type)
            ).pack(side=tk.RIGHT)

            try:
                payload = (
                    _fetch_business_invoice_payload(self.conn, invoice_number)
                    if invoice_type == "business"
                    else _fetch_retail_invoice_payload(self.conn, invoice_number)
                )
            except Exception as exc:
                logging.error(f"User history detail error for {invoice_number}: {exc}")
                ttk.Label(
                    invoice_block,
                    text=f"Failed to load invoice {invoice_number}.",
                    foreground="red"
                ).pack(anchor="w", pady=6)
                continue

            self.render_single_invoice_summary(
                invoice_block,
                payload,
                customer_code,
                customer_role,
                invoice_type
            )
            if invoice_type == "business":
                self.render_business_history_detail(invoice_block, payload)
            else:
                self.render_retail_history_detail(invoice_block, payload)

        self.user_history_detail_canvas.yview_moveto(0)
        for widget in _iter_all_widgets(self.user_history_detail_content):
            _bind_mousewheel_to_canvas(widget, self.user_history_detail_canvas)

    def render_single_invoice_summary(self, parent, payload, customer_code, customer_role, invoice_type):
        header = ttk.LabelFrame(parent, text="Invoice Summary", padding=12)
        header.pack(fill=tk.X, pady=(0, 10))
        summary_rows = [
            ("Customer Name", payload.get("customer_name", "")),
            ("Customer Code", customer_code),
            ("Role", _display_customer_role(customer_role)),
            ("Invoice Number", payload.get("invoice_number", "")),
            ("Invoice Type", "Business" if invoice_type == "business" else "Retail"),
            ("Purchase Date", payload.get("invoice_date_display", "")),
            ("Updated At", payload.get("updated_at_display", "")),
            ("Mobile", payload.get("mobile", "")),
            ("City", payload.get("city", "")),
            ("Shop", payload.get("shop_name", "")),
        ]
        for index, (label, value) in enumerate(summary_rows):
            row_index = index // 2
            column_index = (index % 2) * 2
            ttk.Label(header, text=f"{label}:", width=16, anchor="w").grid(row=row_index, column=column_index, sticky="w", padx=6, pady=4)
            ttk.Label(header, text=str(value), anchor="w").grid(row=row_index, column=column_index + 1, sticky="w", padx=6, pady=4)
        for column_index in range(4):
            header.grid_columnconfigure(column_index, weight=1)

    def refresh_selected_user_history(self):
        selected_user = getattr(self, "user_history_selected_user", None)
        if not selected_user:
            self.render_user_history_empty_state("Select a user to view all invoice details.")
            return
        self.render_user_history_for_user(selected_user)

    def render_retail_history_detail(self, parent, payload):
        items_frame = ttk.LabelFrame(parent, text="Purchased Items", padding=10)
        items_frame.pack(fill=tk.X, pady=(0, 10))

        item_columns = ("category", "product", "qty", "net", "gross", "base", "gst", "final")
        item_tree = ttk.Treeview(items_frame, columns=item_columns, show="headings", height=max(1, min(len(payload["items"]), 6)))
        item_headings = {
            "category": ("Category", 70, "center"),
            "product": ("Product", 150, "w"),
            "qty": ("Qty", 55, "center"),
            "net": ("Net Wt", 80, "e"),
            "gross": ("Gross Wt", 80, "e"),
            "base": ("Base Total", 100, "e"),
            "gst": ("GST Amount", 90, "e"),
            "final": ("Final Total", 100, "e"),
        }
        for column, (label, width, anchor) in item_headings.items():
            item_tree.heading(column, text=label)
            item_tree.column(column, width=width, anchor=anchor)
        for item in payload["items"]:
            qty = float(item[4] or 0)
            item_tree.insert(
                "",
                "end",
                values=(
                    item[16],
                    item[3],
                    f"{qty:g}",
                    f"{float(item[5] or 0) * qty:.3f}",
                    f"{float(item[6] or 0) * qty:.3f}",
                    f"{float(item[10] or 0):.2f}",
                    f"{float(item[12] or 0):.2f}",
                    f"{float(item[13] or 0):.2f}",
                )
            )
        item_scroll = ttk.Scrollbar(items_frame, orient=tk.VERTICAL, command=item_tree.yview)
        item_tree.configure(yscrollcommand=item_scroll.set)
        item_tree.grid(row=0, column=0, sticky="nsew")
        item_scroll.grid(row=0, column=1, sticky="ns")
        items_frame.grid_columnconfigure(0, weight=1)

        exchange_frame = ttk.LabelFrame(parent, text="Exchange Items", padding=10)
        exchange_frame.pack(fill=tk.X, pady=(0, 10))
        if payload["exchange_rows"]:
            exchange_columns = ("description", "net", "purity", "rate", "amount")
            exchange_tree = ttk.Treeview(exchange_frame, columns=exchange_columns, show="headings", height=max(1, min(len(payload["exchange_rows"]), 4)))
            exchange_headings = {
                "description": ("Description", 190, "w"),
                "net": ("Net Wt", 80, "e"),
                "purity": ("Purity %", 80, "e"),
                "rate": ("Rate", 80, "e"),
                "amount": ("Amount", 100, "e"),
            }
            for column, (label, width, anchor) in exchange_headings.items():
                exchange_tree.heading(column, text=label)
                exchange_tree.column(column, width=width, anchor=anchor)
            for row in payload["exchange_rows"]:
                exchange_tree.insert(
                    "",
                    "end",
                    values=(
                        row[0],
                        f"{float(row[1] or 0):.3f}",
                        f"{float(row[2] or 0):.2f}",
                        f"{float(row[3] or 0):.2f}",
                        f"{float(row[4] or 0):.2f}",
                    )
                )
            exchange_tree.pack(fill=tk.X)
        else:
            ttk.Label(exchange_frame, text="No exchange items in this invoice.").pack(anchor="w")

        totals_frame = ttk.LabelFrame(parent, text="Payment Summary", padding=12)
        totals_frame.pack(fill=tk.X, pady=(0, 10))
        rows = [
            ("Items Total", f"{float(payload['items_total'] or 0):.2f}"),
            ("Exchange Less", f"{float(payload['exchange_total'] or 0):.2f}"),
            ("Old Balance Included", f"{float(payload['old_balance'] or 0):.2f}"),
            ("Grand Total", f"{float(payload['grand_total'] or 0):.2f}"),
            ("Amount Paid", f"{float(payload['amount_paid'] or 0):.2f}"),
            ("Remaining Balance", f"{float(payload['remaining_balance'] or 0):.2f}"),
        ]
        for index, (label, value) in enumerate(rows):
            ttk.Label(totals_frame, text=f"{label}:", width=18, anchor="w").grid(row=index // 2, column=(index % 2) * 2, sticky="w", padx=6, pady=4)
            ttk.Label(totals_frame, text=value, anchor="w").grid(row=index // 2, column=(index % 2) * 2 + 1, sticky="w", padx=6, pady=4)

        words_frame = ttk.LabelFrame(parent, text="Amount In Words", padding=12)
        words_frame.pack(fill=tk.X)
        ttk.Label(words_frame, text=payload.get("amount_in_words", ""), wraplength=700, justify="left").pack(anchor="w")

    def render_business_history_detail(self, parent, payload):
        payment_mode_raw = str(payload.get("payment_mode", "") or "")
        payment_mode_norm = payment_mode_raw.strip().lower()
        is_cash_mode = payment_mode_norm in {"price", "cash"}
        payment_mode_display = (
            "Cash" if is_cash_mode else "Fine 24K" if payment_mode_norm == "fine" else payment_mode_raw
        )
        paid_cash_value = payload.get("paid_cash_display") or f"{float(payload['amount_paid'] or 0):.2f}"
        paid_fine_value = payload.get("paid_fine_display") or f"{float(payload['paid_fine_24k'] or 0):.3f}"

        items_frame = ttk.LabelFrame(parent, text="Purchased Items", padding=10)
        items_frame.pack(fill=tk.X, pady=(0, 10))

        item_columns = ("product", "net", "gross", "purity", "wstg", "fine", "rate", "final")
        item_tree = ttk.Treeview(items_frame, columns=item_columns, show="headings", height=max(1, min(len(payload["items"]), 6)))
        item_headings = {
            "product": ("Product", 150, "w"),
            "net": ("Net Wt", 80, "e"),
            "gross": ("Gross Wt", 80, "e"),
            "purity": ("Purity", 70, "e"),
            "wstg": ("WSTG %", 70, "e"),
            "fine": ("Fine 24K", 80, "e"),
            "rate": ("Rate", 80, "e"),
            "final": ("Final Price", 100, "e"),
        }
        for column, (label, width, anchor) in item_headings.items():
            item_tree.heading(column, text=label)
            item_tree.column(column, width=width, anchor=anchor)
        for item in payload["items"]:
            item_tree.insert(
                "",
                "end",
                values=(
                    item[2],
                    f"{float(item[3] or 0):.3f}",
                    f"{float(item[4] or 0):.3f}",
                    f"{float(item[5] or 0):.2f}",
                    f"{float(item[6] or 0):.2f}",
                    f"{float(item[9] or 0):.3f}",
                    f"{float(item[8] or 0):.2f}",
                    f"{float(item[13] or 0):.2f}",
                )
            )
        item_scroll = ttk.Scrollbar(items_frame, orient=tk.VERTICAL, command=item_tree.yview)
        item_tree.configure(yscrollcommand=item_scroll.set)
        item_tree.grid(row=0, column=0, sticky="nsew")
        item_scroll.grid(row=0, column=1, sticky="ns")
        items_frame.grid_columnconfigure(0, weight=1)

        exchange_frame = ttk.LabelFrame(parent, text="Exchange Items", padding=10)
        exchange_frame.pack(fill=tk.X, pady=(0, 10))
        if payload["exchanges"]:
            exchange_columns = ("product", "net", "purity", "fine")
            exchange_tree = ttk.Treeview(exchange_frame, columns=exchange_columns, show="headings", height=max(1, min(len(payload["exchanges"]), 4)))
            exchange_headings = {
                "product": ("Product", 190, "w"),
                "net": ("Net Wt", 90, "e"),
                "purity": ("Purity", 90, "e"),
                "fine": ("Fine 24K", 100, "e"),
            }
            for column, (label, width, anchor) in exchange_headings.items():
                exchange_tree.heading(column, text=label)
                exchange_tree.column(column, width=width, anchor=anchor)
            for row in payload["exchanges"]:
                exchange_tree.insert(
                    "",
                    "end",
                    values=(
                        row[0],
                        f"{float(row[1] or 0):.3f}",
                        f"{float(row[2] or 0):.2f}",
                        f"{float(row[3] or 0):.3f}",
                    )
                )
            exchange_tree.pack(fill=tk.X)
        else:
            ttk.Label(exchange_frame, text="No exchange items in this invoice.").pack(anchor="w")

        totals_frame = ttk.LabelFrame(parent, text="Payment Summary", padding=12)
        totals_frame.pack(fill=tk.X, pady=(0, 10))
        rows = [
            ("Items Total", f"{float(payload['items_total'] or 0):.2f}"),
            ("Grand Total", f"{float(payload['grand_total'] or 0):.2f}"),
            ("Old Cash Balance", f"{float(payload['old_balance_included'] or 0):.2f}"),
            ("Business Fine 24K", f"{float(payload['business_items_fine'] or 0):.3f}"),
            ("Paid Amount", paid_cash_value if is_cash_mode else paid_fine_value),
            ("Paid Mode", "Cash" if is_cash_mode else "Fine 24K"),
            ("Paid Cash", paid_cash_value),
            ("Paid Fine 24K", paid_fine_value),
            ("Remaining Balance", f"{float(payload['remaining_balance'] or 0):.2f}"),
            ("Carry Forward Fine", f"{float(payload['carry_forward_fine'] or 0):.3f}"),
            ("Payment Mode", payment_mode_display),
            ("GST Total", f"{float(payload['gst_total'] or 0):.2f}"),
        ]
        for index, (label, value) in enumerate(rows):
            ttk.Label(totals_frame, text=f"{label}:", width=18, anchor="w").grid(row=index // 2, column=(index % 2) * 2, sticky="w", padx=6, pady=4)
            ttk.Label(totals_frame, text=str(value), anchor="w").grid(row=index // 2, column=(index % 2) * 2 + 1, sticky="w", padx=6, pady=4)

        words_frame = ttk.LabelFrame(parent, text="Amount In Words", padding=12)
        words_frame.pack(fill=tk.X)
        ttk.Label(words_frame, text=payload.get("amount_in_words", ""), wraplength=700, justify="left").pack(anchor="w")

    def print_selected_history_invoice(self):
        selected_user = getattr(self, "user_history_selected_user", None)
        if not selected_user:
            messagebox.showerror("Error", "Please select a user first.")
            return
        customer_code = selected_user.get("customer_code")
        if not customer_code:
            messagebox.showerror("Error", "Customer code not found for selected user.")
            return
        try:
            self.cursor.execute(
                """
                SELECT invoice_number, COALESCE(invoice_type, 'retail')
                FROM invoices
                WHERE customer_code = ?
                ORDER BY invoice_date DESC, invoice_number DESC
                LIMIT 1
                """,
                (customer_code,)
            )
            row = self.cursor.fetchone()
            if not row:
                messagebox.showerror("Error", "No invoices found for selected user.")
                return
            self.print_history_invoice(row[0], row[1])
        except Exception as exc:
            logging.error(f"History print error: {exc}")
            messagebox.showerror("Error", f"Failed to print invoice: {exc}")

    def print_history_invoice(self, invoice_number, invoice_type):
        try:
            if invoice_type == "business":
                print_business_invoice_for(self.conn, invoice_number)
            else:
                print_invoice_for(self.conn, invoice_number)
        except Exception as exc:
            logging.error(f"History print error for {invoice_number}: {exc}")
            messagebox.showerror("Error", f"Failed to print invoice {invoice_number}: {exc}")

    def open_user_history_selector(self):
        try:
            self.cursor.execute("""
                SELECT DISTINCT
                       COALESCE(c.name, ''),
                       i.customer_code,
                       COALESCE(c.mobile, ''),
                       COUNT(i.invoice_number)
                FROM invoices i
                LEFT JOIN customers c ON c.customer_code = i.customer_code
                GROUP BY i.customer_code, c.name, c.mobile
                ORDER BY LOWER(c.name), LOWER(c.mobile)
            """)
            rows = self.cursor.fetchall()
        except sqlite3.Error as e:
            messagebox.showerror("Error", f"Database error: {e}")
            return

        if not rows:
            messagebox.showinfo("Info", "No invoice history found.")
            return

        popup = tk.Toplevel(self.root)
        popup.title("Check Contacts")
        popup.geometry("760x360")
        popup.grab_set()

        ttk.Label(popup, text="Select History Record", font=("Segoe UI", 12, "bold")).pack(pady=(15, 10))

        frame = ttk.Frame(popup, padding=(12, 0, 12, 0))
        frame.pack(fill=tk.BOTH, expand=True)

        columns = ("customer_name", "mobile", "invoice_count")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        tree.heading("customer_name", text="Name")
        tree.heading("mobile", text="Mobile")
        tree.heading("invoice_count", text="Invoices")
        tree.column("customer_name", width=280, anchor="w")
        tree.column("mobile", width=180, anchor="center")
        tree.column("invoice_count", width=120, anchor="center")

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        for index, (name, customer_code, mobile, invoice_count) in enumerate(rows):
            tree.insert("", "end", iid=str(index), values=(name, mobile, invoice_count))

        def use_selected_history():
            selected = tree.selection()
            if not selected:
                messagebox.showerror("Error", "Select a history record first.")
                return
            selected_index = int(selected[0])
            _, customer_code, _, _ = rows[selected_index]
            self.load_user_history(customer_code)
            popup.destroy()

        tree.bind("<Double-1>", lambda _event: use_selected_history())
        ttk.Button(popup, text="Open History", command=use_selected_history).pack(pady=(12, 6))
        ttk.Button(popup, text="Show All", command=lambda: [self.reset_user_history_filters(), popup.destroy()]).pack()


    def enter_shop_name(self):
        self.open_input_popup("Enter Shop Name", "shop_name")

    def enter_owner_name(self):
        self.open_input_popup("Enter Owner Name", "owner_name")

    def enter_making_charges(self):
        popup = tk.Toplevel(self.root)
        popup.title("Enter Making Charges")
        popup.geometry("400x200")

        tk.Label(popup, text="Enter Making Charges for 22c", font=("Arial", 14)).pack(pady=10)

        
        frame_22c = tk.Frame(popup)
        frame_22c.pack(pady=5, padx=20, anchor='w')
        tk.Label(frame_22c, text="22c Making Charges (%):", width=25, anchor='w').pack(side=tk.LEFT)
        entry_22c = tk.Entry(frame_22c, width=20, font=("Arial", 12))
        entry_22c.pack(side=tk.LEFT, padx=10)

       
        frame_24c = tk.Frame(popup)
        frame_24c.pack(pady=5, padx=20, anchor='w')
        tk.Label(frame_24c, text="24c Making Charges (Fixed):", width=25, anchor='w').pack(side=tk.LEFT)
        tk.Label(frame_24c, text="100 Rs/gram", font=("Arial", 12), fg="gray").pack(side=tk.LEFT, padx=10)

        def save_values():
            try:
                making_charges_22c = float(entry_22c.get())
                
                self.cursor.execute("UPDATE settings SET making_charges_22c = ? WHERE id = 1", (making_charges_22c,))
                self.conn.commit()
                messagebox.showinfo("Success", "Making charges updated successfully!")
                popup.destroy()
            except ValueError:
                messagebox.showerror("Error", "Invalid input. Please enter a valid number.")

        
        tk.Button(popup, text="Save", command=save_values).pack(pady=10)
        tk.Button(popup, text="Cancel", command=popup.destroy).pack(pady=5)

    def change_pin(self, pin_type):
        popup = tk.Toplevel(self.root)
        popup.title(f"Change {pin_type.capitalize()} PIN")
        popup.geometry("300x200")

        tk.Label(popup, text=f"Enter New {pin_type.capitalize()} PIN:", font=("Arial", 12)).pack(pady=10)
        new_pin_entry = tk.Entry(popup, show="*", width=20, font=("Arial", 12))
        new_pin_entry.pack(pady=5)

        tk.Label(popup, text=f"Confirm New {pin_type.capitalize()} PIN:", font=("Arial", 12)).pack(pady=10)
        confirm_pin_entry = tk.Entry(popup, show="*", width=20, font=("Arial", 12))
        confirm_pin_entry.pack(pady=5)

        def save_new_pin():
            new_pin = new_pin_entry.get()
            confirm_pin = confirm_pin_entry.get()
            if new_pin != confirm_pin:
                messagebox.showerror("Error", "PINs do not match. Please try again.")
                return
            if not new_pin.isdigit() or len(new_pin) < 4:
                messagebox.showerror("Error", "PIN must be at least 4 digits.")
                return
            self.cursor.execute("UPDATE pins SET pin_value = ? WHERE pin_type = ?", (new_pin, pin_type))
            self.conn.commit()
            messagebox.showinfo("Success", f"{pin_type.capitalize()} PIN updated successfully!")
            popup.destroy()

        tk.Button(popup, text="Save", command=save_new_pin).pack(pady=10)
        tk.Button(popup, text="Cancel", command=popup.destroy).pack(pady=5)

    def open_input_popup(self, title, field, is_float=False):
        popup = tk.Toplevel(self.root)
        popup.title(title)
        popup.geometry("300x150")

        tk.Label(popup, text=title, font=("Arial", 12)).pack(pady=10)
        entry = tk.Entry(popup, width=20, font=("Arial", 12))
        entry.pack(pady=5)

        def save_value():
            value = entry.get()
            try:
                if is_float:
                    value = float(value)
                self.cursor.execute(f"UPDATE settings SET {field} = ? WHERE id = 1", (value,))
                self.conn.commit()
                messagebox.showinfo("Success", f"{title} updated successfully!")
                popup.destroy()
            except ValueError:
                messagebox.showerror("Error", "Invalid input. Please enter a valid value.")

        tk.Button(popup, text="Save", command=save_value).pack(pady=10)
        tk.Button(popup, text="Cancel", command=popup.destroy).pack(pady=5)

    def manage_stock(self):
       
        for widget in self.root.winfo_children():
            widget.destroy()

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.root.configure(bg="#f4f4f4")

       
        container = ttk.Frame(self.root, padding=30)
        container.place(relx=0.5, rely=0.4, anchor="center")

        ttk.Label(
            container,
            text="Manage Stock",
            font=("Segoe UI", 18, "bold")
        ).grid(row=0, column=0, columnspan=2, pady=(0, 20))

        form = ttk.Frame(container)
        form.grid(row=1, column=0, columnspan=2)

       
        ttk.Label(form, text="Product Name:", width=25, anchor="w") \
            .grid(row=0, column=0, padx=10, pady=6, sticky="w")
        product_entry = ttk.Entry(form, width=30)
        product_entry.grid(row=0, column=1, padx=10, pady=6)

        ttk.Label(form, text="Quantity:", width=25, anchor="w") \
            .grid(row=1, column=0, padx=10, pady=6, sticky="w")
        quantity_entry = ttk.Entry(form, width=30)
        quantity_entry.grid(row=1, column=1, padx=10, pady=6)

        ttk.Label(form, text="Net Weight (per qty):", width=25, anchor="w") \
            .grid(row=2, column=0, padx=10, pady=6, sticky="w")
        net_weight_entry = ttk.Entry(form, width=30)
        net_weight_entry.grid(row=2, column=1, padx=10, pady=6)

        ttk.Label(form, text="Gross Weight (per qty):", width=25, anchor="w") \
            .grid(row=3, column=0, padx=10, pady=6, sticky="w")
        gross_weight_entry = ttk.Entry(form, width=30)
        gross_weight_entry.grid(row=3, column=1, padx=10, pady=6)

      
        def add_stock():
            pname = product_entry.get().strip()
            try:
                qty = float(quantity_entry.get())
                net_wt = float(net_weight_entry.get())
                gross_wt = float(gross_weight_entry.get())
            except ValueError:
                messagebox.showerror("Error", "Enter valid numeric values.")
                return

            if not pname or qty <= 0:
                messagebox.showerror("Error", "Product name and positive quantity required.")
                return

            self.cursor.execute(
                "SELECT id, quantity, net_weight, gross_weight FROM stock WHERE product_name = ?",
                (pname,)
            )

            found = False
            for sid, eqty, enet, egross in self.cursor.fetchall():
                per_net = enet / eqty if eqty else 0
                per_gross = egross / eqty if eqty else 0

                if abs(per_net - net_wt) < 1e-6 and abs(per_gross - gross_wt) < 1e-6:
                    self.cursor.execute(
                        "UPDATE stock SET quantity=?, net_weight=?, gross_weight=? WHERE id=?",
                        (
                            eqty + qty,
                            enet + (qty * net_wt),
                            egross + (qty * gross_wt),
                            sid
                        )
                    )
                    found = True
                    break

            if not found:
                self.cursor.execute(
                    "INSERT INTO stock (product_name, quantity, net_weight, gross_weight) VALUES (?, ?, ?, ?)",
                    (pname, qty, qty * net_wt, qty * gross_wt)
                )

         # Delete zero stock
            self.cursor.execute("DELETE FROM stock WHERE quantity <= 0")
            self.conn.commit()

            messagebox.showinfo("Success", "Stock updated successfully.")

            product_entry.delete(0, tk.END)
            quantity_entry.delete(0, tk.END)
            net_weight_entry.delete(0, tk.END)
            gross_weight_entry.delete(0, tk.END)

      
        btn_frame = ttk.Frame(container)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=20)

        ttk.Button(btn_frame, text="Add Stock", command=add_stock).pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="Show Stock", command=self.show_stock).pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="Back", command=self.go_back_callback).pack(fill=tk.X, pady=5)


    def show_stock(self):
       
        for widget in self.root.winfo_children():
            widget.destroy()

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.root.configure(bg="#f4f4f4")

        
        container = ttk.Frame(self.root, padding=25)
        container.place(relx=0.5, rely=0.5, anchor="center")

        ttk.Label(
            container,
            text="Current Stock",
            font=("Segoe UI", 18, "bold")
        ).pack(pady=(0, 15))

        table_frame = ttk.Frame(container)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("Product Name", "Quantity", "Net Weight", "Gross Weight")
        tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=10
        )

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, anchor="center", width=160)

      
        self.cursor.execute(
            "SELECT id, product_name, quantity, net_weight, gross_weight FROM stock WHERE quantity > 0"
        )
        rows = self.cursor.fetchall()

        self.stock_row_map = {} 
        for row in rows:
            sid, pname, qty, net, gross = row
            tree_id = tree.insert("", "end", values=(pname, qty, net, gross))
            self.stock_row_map[tree_id] = sid

        tree.pack(fill=tk.BOTH, expand=True)

        #Delete selected stocks
        def delete_selected_stock():
            selected = tree.selection()
            if not selected:
                messagebox.showerror("Error", "Select a stock row to delete.")
                return

            if not messagebox.askyesno("Confirm", "Delete selected stock?"):
                return

            stock_id = self.stock_row_map[selected[0]]
            self.cursor.execute("DELETE FROM stock WHERE id = ?", (stock_id,))
            self.conn.commit()

            self.show_stock()

    
        btn_frame = ttk.Frame(container)
        btn_frame.pack(pady=15, fill=tk.X)

        ttk.Button(btn_frame, text="Delete Selected", command=delete_selected_stock) \
            .pack(side=tk.LEFT, padx=10, expand=True, fill=tk.X)

        ttk.Button(btn_frame, text="Back", command=self.go_back_callback) \
            .pack(side=tk.LEFT, padx=10, expand=True, fill=tk.X)
        
    def customers_list(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.root.configure(bg="#f4f4f4")

        ttk.Label(
            self.root,
            text="Customers List",
            font=("Segoe UI", 18, "bold")
        ).pack(pady=15)

        filter_frame = ttk.Frame(self.root)
        filter_frame.pack(pady=10)

        current_filter = {"value": "all"}
        selected_customer = {"id": None}
        status_var = tk.StringVar(value="Select a customer")
        role_var = tk.StringVar(value="Customer")

        def load_customers(filter_type="all"):
            current_filter["value"] = filter_type
            for row in tree.get_children():
                tree.delete(row)

            if filter_type == "active":
                self.cursor.execute("""
                    SELECT id, name, city, mobile, customer_code,
                           COALESCE(role, 'Customer'),
                           COALESCE(balance, 0),
                           COALESCE(fine_balance, 0),
                           is_active
                    FROM customers
                    WHERE is_active = 1
                    ORDER BY id DESC
                """)
            elif filter_type == "inactive":
                self.cursor.execute("""
                    SELECT id, name, city, mobile, customer_code,
                           COALESCE(role, 'Customer'),
                           COALESCE(balance, 0),
                           COALESCE(fine_balance, 0),
                           is_active
                    FROM customers
                    WHERE is_active = 0
                    ORDER BY id DESC
                """)
            else:
                self.cursor.execute("""
                    SELECT id, name, city, mobile, customer_code,
                           COALESCE(role, 'Customer'),
                           COALESCE(balance, 0),
                           COALESCE(fine_balance, 0),
                           is_active
                    FROM customers
                    ORDER BY id DESC
                """)

            for row in self.cursor.fetchall():
                status = "Active" if row[8] == 1 else "Inactive"
                tree.insert(
                    "",
                    "end",
                    values=(
                        row[0],
                        row[4],
                        row[1],
                        row[2],
                        row[3],
                        _display_customer_role(row[5]),
                        f"{float(row[6] or 0):.2f}",
                        f"{float(row[7] or 0):.3f}",
                        status
                    )
                )

        ttk.Button(filter_frame, text="Show All", command=lambda: load_customers("all")).pack(side=tk.LEFT, padx=5)
        ttk.Button(filter_frame, text="Active Only", command=lambda: load_customers("active")).pack(side=tk.LEFT, padx=5)
        ttk.Button(filter_frame, text="Inactive Only", command=lambda: load_customers("inactive")).pack(side=tk.LEFT, padx=5)

        columns = (
            "ID", "Customer Code", "Name", "City", "Mobile",
            "Role", "Balance Rs", "Fine 24K", "Status"
        )

        table_frame = ttk.Frame(self.root)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=18
        )

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, anchor="center", width=120)

        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=y_scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        def sync_selection(_event=None):
            selected = tree.selection()
            if not selected:
                selected_customer["id"] = None
                status_var.set("Select a customer")
                role_var.set("Customer")
                return

            values = tree.item(selected[0])["values"]
            selected_customer["id"] = values[0]
            status_var.set(values[8])
            role_var.set(values[5] if values[5] in {"Customer", "Businessman"} else _display_customer_role(values[5]))

        info_frame = ttk.Frame(self.root)
        info_frame.pack(fill=tk.X, padx=20, pady=(0, 8))
        ttk.Label(info_frame, text="Status", width=10, anchor="w").pack(side=tk.LEFT)
        ttk.Label(info_frame, textvariable=status_var, width=14, anchor="w").pack(side=tk.LEFT, padx=(0, 20))
        ttk.Label(info_frame, text="Role", width=8, anchor="w").pack(side=tk.LEFT)
        role_combo = ttk.Combobox(
            info_frame,
            textvariable=role_var,
            values=("Customer", "Businessman"),
            state="readonly",
            width=14
        )
        role_combo.pack(side=tk.LEFT)

        load_customers("all")

        def deactivate_customer():
            selected = tree.selection()
            if not selected:
                messagebox.showerror("Error", "Please select a customer.")
                return

            values = tree.item(selected[0])["values"]
            cust_id = values[0]
            balance = float(values[6] or 0)
            fine_balance = float(values[7] or 0)

            if balance > 0 or fine_balance > 0:
                messagebox.showerror(
                    "Error",
                    "Customer has remaining old balance. Cannot deactivate."
                )
                return

            self.cursor.execute(
                "UPDATE customers SET is_active = 0 WHERE id = ?",
                (cust_id,)
            )
            self.conn.commit()
            load_customers(current_filter["value"])

        def activate_customer():
            selected = tree.selection()
            if not selected:
                messagebox.showerror("Error", "Please select a customer.")
                return

            values = tree.item(selected[0])["values"]
            cust_id = values[0]

            self.cursor.execute(
                "UPDATE customers SET is_active = 1 WHERE id = ?",
                (cust_id,)
            )
            self.conn.commit()
            load_customers(current_filter["value"])

        def update_customer_role():
            customer_id = selected_customer["id"]
            if not customer_id:
                messagebox.showerror("Error", "Please select a customer.")
                return

            normalized_role = _normalize_customer_role(role_var.get())
            if normalized_role == "BusinessMan":
                self.cursor.execute(
                    "UPDATE customers SET role = ? WHERE id = ?",
                    (normalized_role, customer_id)
                )
            else:
                self.cursor.execute(
                    "UPDATE customers SET role = ?, fine_balance = 0 WHERE id = ?",
                    (normalized_role, customer_id)
                )
            self.conn.commit()
            load_customers(current_filter["value"])
            messagebox.showinfo("Success", f"Customer role updated to {_display_customer_role(normalized_role)}.")

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(pady=10)

        ttk.Button(btn_frame, text="Deactivate Customer", command=deactivate_customer).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="Activate Customer", command=activate_customer).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="Update Role", command=update_customer_role).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="Back", command=self.go_back_callback).pack(side=tk.LEFT, padx=10)

        tree.bind("<<TreeviewSelect>>", sync_selection)

    def open_invoice_reports_page(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        header = ttk.Label(
            self.root,
            text="Invoice Reports",
            font=("Segoe UI", 18, "bold")
        )
        header.pack(pady=15)

        filter_frame = ttk.Frame(self.root, padding=10)
        filter_frame.pack(fill=tk.X)

        ttk.Label(filter_frame, text="From (DD.MM.YYYY):").grid(row=0, column=0, padx=5)
        self.invoice_report_from_date_entry = ttk.Entry(filter_frame, width=15)
        self.invoice_report_from_date_entry.grid(row=0, column=1, padx=5)

        ttk.Label(filter_frame, text="Up To (DD.MM.YYYY):").grid(row=0, column=2, padx=5)
        self.invoice_report_to_date_entry = ttk.Entry(filter_frame, width=15)
        self.invoice_report_to_date_entry.grid(row=0, column=3, padx=5)

        ttk.Label(filter_frame, text="Role:").grid(row=0, column=4, padx=5)
        self.invoice_report_role_var = tk.StringVar(value="All Roles")
        self.invoice_report_role_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.invoice_report_role_var,
            values=("All Roles", "Customer", "Businessman"),
            state="readonly",
            width=14
        )
        self.invoice_report_role_combo.grid(row=0, column=5, padx=5)

        ttk.Button(
            filter_frame,
            text="Generate Report",
            command=self.generate_invoice_report
        ).grid(row=0, column=6, padx=10)

        self.invoice_report_message = ttk.Label(
            self.root,
            text="",
            font=("Segoe UI", 10)
        )
        self.invoice_report_message.pack(pady=(0, 5))

        table_wrap = ttk.Frame(self.root)
        table_wrap.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        columns = (
            "invoice_number",
            "invoice_date",
            "customer_code",
            "customer_name",
            "mobile",
            "customer_role",
            "invoice_type",
            "total_products",
            "exchange_items",
            "items_total",
            "exchange_total",
            "old_balance",
            "grand_total",
            "amount_paid",
            "remaining_balance",
            "carry_forward_fine",
        )

        self.invoice_report_tree = ttk.Treeview(
            table_wrap,
            columns=columns,
            show="headings",
            height=16
        )

        headings = {
            "invoice_number": "Invoice Number",
            "invoice_date": "Date",
            "customer_code": "Customer Code",
            "customer_name": "Customer Name",
            "mobile": "Mobile",
            "customer_role": "Role",
            "invoice_type": "Type",
            "total_products": "Total Products",
            "exchange_items": "Exchange Items",
            "items_total": "Items Total",
            "exchange_total": "Exchange Total",
            "old_balance": "Old Balance",
            "grand_total": "Grand Total",
            "amount_paid": "Amount Paid",
            "remaining_balance": "Remaining Balance",
            "carry_forward_fine": "Carry Forward Fine",
        }

        widths = {
            "invoice_number": 110,
            "invoice_date": 90,
            "customer_code": 110,
            "customer_name": 150,
            "mobile": 110,
            "customer_role": 90,
            "invoice_type": 90,
            "total_products": 95,
            "exchange_items": 95,
            "items_total": 95,
            "exchange_total": 105,
            "old_balance": 95,
            "grand_total": 95,
            "amount_paid": 95,
            "remaining_balance": 120,
            "carry_forward_fine": 120,
        }

        for col in columns:
            self.invoice_report_tree.heading(col, text=headings[col])
            self.invoice_report_tree.column(col, width=widths[col], anchor="center")

        y_scroll = ttk.Scrollbar(
            table_wrap,
            orient=tk.VERTICAL,
            command=self.invoice_report_tree.yview
        )
        x_scroll = ttk.Scrollbar(
            table_wrap,
            orient=tk.HORIZONTAL,
            command=self.invoice_report_tree.xview
        )
        self.invoice_report_tree.configure(
            yscrollcommand=y_scroll.set,
            xscrollcommand=x_scroll.set
        )

        self.invoice_report_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_wrap.grid_rowconfigure(0, weight=1)
        table_wrap.grid_columnconfigure(0, weight=1)

        self.invoice_report_totals_frame = ttk.LabelFrame(
            self.root,
            text="Report Totals",
            padding=10
        )
        self.invoice_report_totals_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.invoice_report_total_labels = {}
        total_fields = [
            ("total_invoices", "Total Invoices"),
            ("total_products", "Total Products"),
            ("total_exchange_items", "Total Exchange Items"),
            ("sum_items_total", "Items Total"),
            ("sum_exchange_total", "Exchange Total"),
            ("sum_old_balance", "Old Balance"),
            ("sum_grand_total", "Grand Total"),
            ("sum_amount_paid", "Amount Paid"),
            ("sum_remaining_balance", "Remaining Balance"),
        ]

        for index, (key, label) in enumerate(total_fields):
            ttk.Label(
                self.invoice_report_totals_frame,
                text=f"{label}:",
                font=("Segoe UI", 10, "bold")
            ).grid(row=index // 3, column=(index % 3) * 2, sticky="e", padx=6, pady=4)
            value_label = ttk.Label(
                self.invoice_report_totals_frame,
                text="0",
                font=("Segoe UI", 10)
            )
            value_label.grid(row=index // 3, column=(index % 3) * 2 + 1, sticky="w", padx=6, pady=4)
            self.invoice_report_total_labels[key] = value_label

        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        ttk.Button(
            action_frame,
            text="Back",
            command=self.create_admin_login_page
        ).pack(side=tk.RIGHT)

        self.update_invoice_report_totals()

    def update_invoice_report_totals(self, totals=None):
        totals = totals or {}

        defaults = {
            "total_invoices": 0,
            "total_products": 0,
            "total_exchange_items": 0,
            "sum_items_total": 0.0,
            "sum_exchange_total": 0.0,
            "sum_old_balance": 0.0,
            "sum_grand_total": 0.0,
            "sum_amount_paid": 0.0,
            "sum_remaining_balance": 0.0,
        }
        defaults.update(totals)

        integer_keys = {"total_invoices", "total_products", "total_exchange_items"}

        for key, label in self.invoice_report_total_labels.items():
            value = defaults.get(key, 0)
            if key in integer_keys:
                label.config(text=str(int(value)))
            else:
                label.config(text=f"{float(value):.2f}")

    def generate_invoice_report(self):
        for row in self.invoice_report_tree.get_children():
            self.invoice_report_tree.delete(row)

        self.invoice_report_message.config(text="")
        self.update_invoice_report_totals()

        try:
            from_date = datetime.strptime(
                self.invoice_report_from_date_entry.get(),
                "%d.%m.%Y"
            ).strftime("%Y-%m-%d")

            to_date = datetime.strptime(
                self.invoice_report_to_date_entry.get(),
                "%d.%m.%Y"
            ).strftime("%Y-%m-%d")
        except ValueError:
            messagebox.showerror(
                "Error",
                "Enter dates in DD.MM.YYYY format."
            )
            return

        role_filter = _normalize_report_role_filter(
            self.invoice_report_role_var.get()
            if hasattr(self, "invoice_report_role_var")
            else None
        )

        query = """
            SELECT
                i.invoice_number,
                i.invoice_date,
                i.customer_code,
                COALESCE(c.name, ''),
                COALESCE(c.mobile, ''),
                COALESCE(NULLIF(i.customer_role, ''), NULLIF(c.role, ''), 'Customer'),
                COALESCE(i.invoice_type, 'retail'),
                COALESCE(sales.total_products, 0) AS total_products,
                COALESCE(exchanges.exchange_items, 0) AS exchange_items,
                COALESCE(i.items_total, 0),
                COALESCE(i.exchange_total, 0),
                COALESCE(i.old_balance_included, 0),
                COALESCE(i.grand_total, 0),
                COALESCE(i.amount_paid, 0),
                COALESCE(i.remaining_balance, 0),
                COALESCE(i.carry_forward_fine, 0)
            FROM invoices i
            LEFT JOIN customers c
                ON c.customer_code = i.customer_code
            LEFT JOIN (
                SELECT
                    invoice_number,
                    SUM(total_products) AS total_products
                FROM (
                    SELECT invoice_number, SUM(COALESCE(quantity, 0)) AS total_products
                    FROM invoice_details
                    GROUP BY invoice_number
                    UNION ALL
                    SELECT invoice_number, COUNT(*) AS total_products
                    FROM business_invoice_details
                    GROUP BY invoice_number
                )
                GROUP BY invoice_number
            ) AS sales
                ON sales.invoice_number = i.invoice_number
            LEFT JOIN (
                SELECT
                    invoice_number,
                    SUM(exchange_items) AS exchange_items
                FROM (
                    SELECT invoice_number, COUNT(*) AS exchange_items
                    FROM exchange_details
                    GROUP BY invoice_number
                    UNION ALL
                    SELECT invoice_number, COUNT(*) AS exchange_items
                    FROM business_exchange_details
                    GROUP BY invoice_number
                )
                GROUP BY invoice_number
            ) AS exchanges
                ON exchanges.invoice_number = i.invoice_number
            WHERE substr(i.invoice_date, 1, 10) BETWEEN ? AND ?
            ORDER BY i.invoice_date ASC, i.invoice_number ASC
        """
        params = [from_date, to_date]

        if role_filter:
            query = query.replace(
                "WHERE substr(i.invoice_date, 1, 10) BETWEEN ? AND ?",
                """WHERE substr(i.invoice_date, 1, 10) BETWEEN ? AND ?
            AND LOWER(COALESCE(NULLIF(i.customer_role, ''), NULLIF(c.role, ''), 'Customer')) = ?"""
            )
            params.append(role_filter.lower())

        cursor = self.conn.cursor()
        cursor.execute(query, params)

        rows = cursor.fetchall()

        totals = {
            "total_invoices": 0,
            "total_products": 0,
            "total_exchange_items": 0,
            "sum_items_total": 0.0,
            "sum_exchange_total": 0.0,
            "sum_old_balance": 0.0,
            "sum_grand_total": 0.0,
            "sum_amount_paid": 0.0,
            "sum_remaining_balance": 0.0,
        }

        if not rows:
            selected_role_label = (
                _display_customer_role(role_filter) if role_filter else "All Roles"
            )
            self.invoice_report_message.config(
                text=f"No records found for the selected dates and role ({selected_role_label})."
            )
            self.update_invoice_report_totals(totals)
            return

        for row in rows:
            invoice_date_display = row[1]
            try:
                invoice_date_display = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %I:%M %p")
            except Exception:
                try:
                    invoice_date_display = datetime.strptime(row[1], "%Y-%m-%d").strftime("%d.%m.%Y")
                except Exception:
                    pass

            self.invoice_report_tree.insert(
                "",
                "end",
                values=(
                    row[0],
                    invoice_date_display,
                    row[2],
                    row[3],
                    row[4],
                    _display_customer_role(row[5]),
                    row[6],
                    int(float(row[7] or 0)),
                    int(float(row[8] or 0)),
                    f"{float(row[9] or 0):.2f}",
                    f"{float(row[10] or 0):.2f}",
                    f"{float(row[11] or 0):.2f}",
                    f"{float(row[12] or 0):.2f}",
                    f"{float(row[13] or 0):.2f}",
                    f"{float(row[14] or 0):.2f}",
                    f"{float(row[15] or 0):.3f}",
                )
            )

            totals["total_invoices"] += 1
            totals["total_products"] += int(float(row[7] or 0))
            totals["total_exchange_items"] += int(float(row[8] or 0))
            totals["sum_items_total"] += float(row[9] or 0)
            totals["sum_exchange_total"] += float(row[10] or 0)
            totals["sum_old_balance"] += float(row[11] or 0)
            totals["sum_grand_total"] += float(row[12] or 0)
            totals["sum_amount_paid"] += float(row[13] or 0)
            totals["sum_remaining_balance"] += float(row[14] or 0)

        self.update_invoice_report_totals(totals)
        selected_role_label = (
            _display_customer_role(role_filter) if role_filter else "All Roles"
        )
        self.invoice_report_message.config(
            text=f"{totals['total_invoices']} invoice(s) found for {selected_role_label}."
        )

    def open_gst_reports_page(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        header = ttk.Label(
            self.root,
            text="GST Reports",
            font=("Segoe UI", 18, "bold")
        )
        header.pack(pady=15)

        filter_frame = ttk.Frame(self.root, padding=10)
        filter_frame.pack(fill=tk.X)

        ttk.Label(filter_frame, text="From (DD.MM.YYYY):").grid(row=0, column=0, padx=5)
        self.from_date_entry = ttk.Entry(filter_frame, width=15)
        self.from_date_entry.grid(row=0, column=1, padx=5)

        ttk.Label(filter_frame, text="Up To (DD.MM.YYYY):").grid(row=0, column=2, padx=5)
        self.to_date_entry = ttk.Entry(filter_frame, width=15)
        self.to_date_entry.grid(row=0, column=3, padx=5)

        ttk.Label(filter_frame, text="Role:").grid(row=0, column=4, padx=5)
        self.gst_report_role_var = tk.StringVar(value="All Roles")
        self.gst_report_role_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.gst_report_role_var,
            values=("All Roles", "Customer", "Businessman"),
            state="readonly",
            width=14
        )
        self.gst_report_role_combo.grid(row=0, column=5, padx=5)

        ttk.Button(
            filter_frame,
            text="Generate Report",
            command=self.generate_gst_report
        ).grid(row=0, column=6, padx=10)

        self.report_frame = ttk.Frame(self.root)
        self.report_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Button(
            self.root,
            text="Back",
            command=self.create_admin_login_page
        ).pack(pady=10)   


    def generate_gst_report(self):
        for widget in self.report_frame.winfo_children():
            widget.destroy()

        try:
            from_date = datetime.strptime(
                self.from_date_entry.get(),
                "%d.%m.%Y"
            ).strftime("%Y-%m-%d")

            to_date = datetime.strptime(
                self.to_date_entry.get(),
                "%d.%m.%Y"
            ).strftime("%Y-%m-%d")

        except ValueError:
            messagebox.showerror(
                "Error",
                "Enter dates in DD.MM.YYYY format."
            )
            return

        role_filter = _normalize_report_role_filter(
            self.gst_report_role_var.get()
            if hasattr(self, "gst_report_role_var")
            else None
        )

        query = """
            SELECT
                i.invoice_number,
                COALESCE(c.mobile, ''),
                COALESCE(NULLIF(i.customer_role, ''), NULLIF(c.role, ''), 'Customer') AS customer_role,
                COALESCE(gst_data.total_gst, 0) AS total_gst,
                COALESCE(i.grand_total, 0) AS invoice_total,
                COALESCE(i.amount_paid, 0) AS amount_paid,
                COALESCE(i.remaining_balance, 0) AS remaining_balance
            FROM invoices i
            LEFT JOIN customers c
                ON c.customer_code = i.customer_code
            LEFT JOIN (
                SELECT
                    invoice_number,
                    SUM(gst_amount) AS total_gst
                FROM (
                    SELECT invoice_number, COALESCE(gst_amount, 0) AS gst_amount
                    FROM invoice_details
                    UNION ALL
                    SELECT invoice_number, COALESCE(gst_amount, 0) AS gst_amount
                    FROM business_invoice_details
                )
                GROUP BY invoice_number
            ) AS gst_data
                ON gst_data.invoice_number = i.invoice_number
            WHERE substr(i.invoice_date, 1, 10) BETWEEN ? AND ?
            ORDER BY i.invoice_date ASC, i.invoice_number ASC
        """
        params = [from_date, to_date]

        if role_filter:
            query = query.replace(
                "WHERE substr(i.invoice_date, 1, 10) BETWEEN ? AND ?",
                """WHERE substr(i.invoice_date, 1, 10) BETWEEN ? AND ?
            AND LOWER(COALESCE(NULLIF(i.customer_role, ''), NULLIF(c.role, ''), 'Customer')) = ?"""
            )
            params.append(role_filter.lower())

        cursor = self.conn.cursor()
        cursor.execute(query, params)

        rows = cursor.fetchall()

        if not rows:
            selected_role_label = (
                _display_customer_role(role_filter) if role_filter else "All Roles"
            )
            tk.Label(
                self.report_frame,
                text=f"No records found for selected filters (Role: {selected_role_label}).",
                font=("Arial", 12)
            ).pack()
            return

        columns = [
            "Invoice Number",
            "Phone Number",
            "Role",
            "GST Amount",
            "Total Amount (After GST)",
            "Amount Paid",
            "Remaining Amount"
        ]

        for col_index, col_name in enumerate(columns):
            tk.Label(
                self.report_frame,
                text=col_name,
                bg="darkgreen",
                fg="white",
                font=("Arial", 11, "bold"),
                borderwidth=1,
                relief="solid"
            ).grid(row=0, column=col_index, sticky="nsew")

        total_gst_sum = 0.0

        for row_index, row in enumerate(rows, start=1):
            for col_index, value in enumerate(row):
                if col_index == 2:
                    text_value = _display_customer_role(value)
                elif isinstance(value, float):
                    text_value = f"{value:.2f}"
                else:
                    text_value = value
                tk.Label(
                    self.report_frame,
                    text=text_value,
                    borderwidth=1,
                    relief="solid"
                ).grid(row=row_index, column=col_index, sticky="nsew")

            total_gst_sum += float(row[3] or 0)

        # Add GST Total
        tk.Label(
            self.report_frame,
            text="Total GST Collected:",
            font=("Arial", 12, "bold")
        ).grid(row=len(rows) + 1, column=2, sticky="e")

        tk.Label(
            self.report_frame,
            text=f"{total_gst_sum:.2f}",
            font=("Arial", 12, "bold")
        ).grid(row=len(rows) + 1, column=3, sticky="w")
            
if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    root = tk.Tk()
    app =InvoiceApp(root)
    root.mainloop()

 
