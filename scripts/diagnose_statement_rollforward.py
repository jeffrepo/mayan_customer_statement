"""Diagnostica la continuidad mensual del estado de cuenta Mayan.

Uso previsto (desde la raiz del addon):

    odoo-bin shell -d NOMBRE_BD < scripts/diagnose_statement_rollforward.py

El script solo ejecuta busquedas y calculos. No crea, modifica ni elimina
registros. La configuracion se recibe mediante variables de entorno; consulte
el README del addon para ver un ejemplo completo.
"""

import calendar
import os
from datetime import date
from decimal import Decimal, InvalidOperation


INCLUDED_PAYMENT_STATES = {"in_process", "paid"}
REFERENCE_SETTINGS = (
    ("STATEMENT_REFERENCE_OPENING", "Saldo inicial"),
    ("STATEMENT_REFERENCE_CHARGES", "Cargos del mes"),
    ("STATEMENT_REFERENCE_PAYMENTS", "Abonos del mes"),
    ("STATEMENT_REFERENCE_CLOSING", "Saldo al corte"),
)


def _setting(name, default=None):
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _integer_setting(name, default):
    value = _setting(name, str(default))
    try:
        return int(value)
    except ValueError as error:
        raise RuntimeError("%s debe ser un numero entero." % name) from error


def _amount_setting(name):
    value = _setting(name)
    if value is None:
        return None
    try:
        return float(Decimal(value.replace(",", "")))
    except InvalidOperation as error:
        raise RuntimeError("%s debe contener un importe valido." % name) from error


def _period_dates(year, month):
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _next_period(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _money(currency, amount):
    amount = currency.round(amount)
    digits = currency.decimal_places
    number = ("{:,.%sf}" % digits).format(abs(amount))
    sign = "-" if amount < 0 else ""
    symbol = currency.symbol or currency.name or ""
    if currency.position == "after":
        return "%s%s %s" % (sign, number, symbol)
    return "%s%s %s" % (sign, symbol, number)


def _same_amount(currency, left, right):
    return currency.is_zero(left - right)


def _heading(title):
    print("\n%s" % title)
    print("=" * len(title))


def _company(env):
    company_id = _integer_setting("STATEMENT_COMPANY_ID", env.company.id)
    company = env["res.company"].sudo().browse(company_id).exists()
    if not company:
        raise RuntimeError("No existe la compania con ID %s." % company_id)
    return company


def _code_value(field, code):
    if field.type == "integer":
        try:
            return int(code)
        except ValueError:
            return -1
    return code


def _partner(env, company):
    Partner = env["res.partner"].sudo().with_company(company)
    partner_id = _setting("STATEMENT_PARTNER_ID")
    if partner_id:
        partner = Partner.browse(_integer_setting("STATEMENT_PARTNER_ID", 0)).exists()
        if not partner:
            raise RuntimeError("No existe el cliente indicado por ID.")
        return partner

    code = _setting("STATEMENT_PARTNER_CODE", "1836")
    domains = []
    code_field = Partner._fields.get("codigo_socio")
    if code_field:
        domains.append([("codigo_socio", "=", _code_value(code_field, code))])
    domains.append([("ref", "=", code)])

    partners = Partner.browse()
    for domain in domains:
        partners |= Partner.search(domain)
    if not partners:
        raise RuntimeError(
            "No se encontro un cliente con codigo_socio o ref igual a %s." % code
        )

    commercial_partners = partners.mapped("commercial_partner_id")
    if len(commercial_partners) > 1:
        matches = ", ".join(
            "%s (ID %s)" % (item.display_name, item.id)
            for item in commercial_partners
        )
        raise RuntimeError("El codigo coincide con varios clientes: %s." % matches)
    return partners.sorted(key=lambda item: item.id)[0]


def _wizard(env, company, partner, year, month):
    try:
        Wizard = env["mayan.customer.statement.wizard"]
    except KeyError as error:
        raise RuntimeError(
            "Instale o actualice el addon mayan_customer_statement antes de ejecutar "
            "el diagnostico."
        ) from error
    return Wizard.sudo().with_company(company).new(
        {
            "company_id": company.id,
            "partner_ids": [(6, 0, [partner.id])],
            "year": year,
            "month": str(month),
        }
    )


def _move_category(wizard, move):
    category = wizard._statement_move_category(move)
    if category == "credit_note":
        return "nota_credito", True
    if category == "purchase":
        return "compras_cadis", True
    if category == "other_charge":
        return "otros_cargos", True
    return "no_incluida", False


def _period_analysis(wizard, partner, date_from, date_to):
    moves = wizard._get_moves(partner, date_to)
    rows = []

    for move in moves:
        effective_date = wizard._move_effective_date(move)
        if not effective_date or not date_from <= effective_date <= date_to:
            continue
        category, included = _move_category(wizard, move)
        amount = abs(move.amount_total_signed)
        rows.append(
            {
                "record": move,
                "effective_date": effective_date,
                "amount": amount,
                "category": category,
                "included": included,
                "journal": move.journal_id.display_name,
                "journal_other_charges": move.journal_id.mayan_otros_cargos,
            }
        )
    return rows


def _all_period_payments(wizard, partner, date_from, date_to):
    Payment = wizard.env["account.payment"].sudo().with_company(wizard.company_id)
    return Payment.search(
        [
            ("company_id", "=", wizard.company_id.id),
            ("partner_id", "child_of", partner.commercial_partner_id.id),
            ("partner_type", "=", "customer"),
            ("payment_type", "=", "inbound"),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
        ],
        order="date, name, id",
    )


def _payment_document(payment):
    document = ""
    if "numero_recibo" in payment._fields:
        document = payment.numero_recibo or ""
    return document or payment.payment_reference or payment.name or ""


def _payment_company_amount(payment):
    return abs(payment.amount_company_currency_signed)


def _receivable_lines(wizard, partner, date_from, date_to):
    Line = wizard.env["account.move.line"].sudo().with_company(wizard.company_id)
    base_domain = [
        ("company_id", "=", wizard.company_id.id),
        ("partner_id", "child_of", partner.commercial_partner_id.id),
        ("parent_state", "=", "posted"),
        ("account_id.account_type", "=", "asset_receivable"),
    ]
    opening_lines = Line.search(base_domain + [("date", "<", date_from)])
    period_lines = Line.search(
        base_domain + [("date", ">=", date_from), ("date", "<=", date_to)],
        order="date, move_name, id",
    )
    return opening_lines, period_lines


def _print_summary(currency, statement, date_from, date_to):
    print(
        "Periodo: %s al %s"
        % (date_from.strftime("%d/%m/%Y"), date_to.strftime("%d/%m/%Y"))
    )
    for label, key in (
        ("Saldo anterior", "saldo_anterior"),
        ("Compras y cadis", "compras_cadis"),
        ("Otros cargos", "otros_cargos"),
        ("Subtotal cargos", "subtotal_cargos"),
        ("Pagos", "pagos"),
        ("Saldo al corte", "saldo_corte"),
    ):
        print("  %-20s %s" % (label + ":", _money(currency, statement[key])))


def _print_moves(currency, rows):
    _heading("Documentos del mes y clasificacion")
    if not rows:
        print("No se encontraron facturas o notas de credito en el periodo.")
        return
    for row in rows:
        move = row["record"]
        invoice_date = (
            move.invoice_date.strftime("%d/%m/%Y") if move.invoice_date else "-"
        )
        effective_date = row["effective_date"].strftime("%d/%m/%Y")
        status = "SI" if row["included"] else "NO"
        print(
            "%s | factura %s | efectiva %s | %s | %s | incluida: %s"
            % (
                move.name or "(sin numero)",
                invoice_date,
                effective_date,
                _money(currency, row["amount"]),
                row["category"],
                status,
            )
        )
        print("  ref: %s" % (move.ref or "-"))
        print(
            "  diario: %s | otros cargos: %s"
            % (
                row["journal"],
                "SI" if row["journal_other_charges"] else "NO",
            )
        )


def _print_receivable_adjustments(
    currency,
    wizard,
    partner,
    date_from,
    date_to,
):
    moves = wizard._get_moves(partner, date_to)
    payments = wizard._get_payments(partner, date_to)
    adjustments = wizard._get_receivable_adjustments(
        partner,
        date_to,
        moves=moves,
        payments=payments,
    ).filtered(lambda line: date_from <= line.date <= date_to)

    _heading("Apuntes directos de cuentas por cobrar")
    if not adjustments:
        print("No se encontraron apuntes directos en el periodo.")
        return

    for line in adjustments:
        category = wizard._statement_receivable_category(line)
        print(
            "%s | %s | %s | %s | %s"
            % (
                line.date.strftime("%d/%m/%Y"),
                line.move_id.name or "(sin numero)",
                line.name or line.move_id.ref or "-",
                category,
                _money(currency, line.balance),
            )
        )
        print(
            "  diario: %s | otros cargos: %s"
            % (
                line.move_id.journal_id.display_name,
                "SI"
                if line.move_id.journal_id.mayan_otros_cargos
                else "NO",
            )
        )


def _print_payments(currency, wizard, partner, date_from, date_to):
    _heading("Pagos de cliente del mes")
    included = wizard._get_payments(partner, date_to).filtered(
        lambda payment: date_from <= payment.date <= date_to
    )
    included_ids = set(included.ids)
    candidates = _all_period_payments(wizard, partner, date_from, date_to)
    if not candidates:
        print("No se encontraron pagos de cliente en account.payment.")
    for payment in candidates:
        amount = _payment_company_amount(payment)
        status = "SI" if payment.id in included_ids else "NO"
        reason = ""
        if payment.id not in included_ids:
            if payment.state not in INCLUDED_PAYMENT_STATES:
                reason = " (estado no admitido por el reporte)"
            else:
                reason = " (revisar dominio de cliente/compania)"
        if not amount:
            reason += " (importe en moneda de compania es cero)"
        print(
            "%s | %s | estado %s | %s | incluido: %s%s"
            % (
                payment.date.strftime("%d/%m/%Y"),
                _payment_document(payment),
                payment.state,
                _money(currency, amount),
                status,
                reason,
            )
        )
        if payment.currency_id != currency:
            print(
                "  moneda original: %s %s"
                % (payment.currency_id.name, payment.amount)
            )
    missing_from_candidates = included.filtered(
        lambda item: item.id not in candidates.ids
    )
    for payment in missing_from_candidates:
        print(
            "ADVERTENCIA: %s fue incluido por el reporte, pero no aparecio en la "
            "busqueda amplia del periodo." % _payment_document(payment)
        )


def _print_receivable_ledger(currency, opening_lines, period_lines):
    _heading("Mayor contable de cuentas por cobrar")
    opening = sum(opening_lines.mapped("balance"))
    debit = sum(line.balance for line in period_lines if line.balance > 0)
    credit = abs(sum(line.balance for line in period_lines if line.balance < 0))
    closing = opening + sum(period_lines.mapped("balance"))
    print("  Saldo contable inicial: %s" % _money(currency, opening))
    print("  Debitos del periodo:    %s" % _money(currency, debit))
    print("  Creditos del periodo:   %s" % _money(currency, credit))
    print("  Saldo contable final:   %s" % _money(currency, closing))
    print("\nMovimientos del periodo:")
    if not period_lines:
        print("  No hay apuntes contables de cuentas por cobrar.")
    for line in period_lines:
        print(
            "%s | %s | %s | debito %s | credito %s | saldo %s"
            % (
                line.date.strftime("%d/%m/%Y"),
                line.move_name or line.move_id.name or "(sin numero)",
                line.name or "-",
                _money(currency, line.debit),
                _money(currency, line.credit),
                _money(currency, line.balance),
            )
        )
    return opening, debit, credit, closing


def _print_reference(currency, statement):
    reference = {
        name: _amount_setting(name) for name, unused_label in REFERENCE_SETTINGS
    }
    if not any(value is not None for value in reference.values()):
        return

    _heading("Comparacion con reporte historico")
    statement_keys = {
        "STATEMENT_REFERENCE_OPENING": "saldo_anterior",
        "STATEMENT_REFERENCE_CHARGES": "subtotal_cargos",
        "STATEMENT_REFERENCE_PAYMENTS": "pagos",
        "STATEMENT_REFERENCE_CLOSING": "saldo_corte",
    }
    labels = dict(REFERENCE_SETTINGS)
    for name, expected in reference.items():
        if expected is None:
            continue
        observed = statement[statement_keys[name]]
        print(
            "%-18s historico %s | nuevo %s | diferencia %s"
            % (
                labels[name] + ":",
                _money(currency, expected),
                _money(currency, observed),
                _money(currency, observed - expected),
            )
        )


def _print_continuity(
    currency, statement, next_statement, rows, next_year, next_month
):
    _heading("Continuidad hacia %02d/%s" % (next_month, next_year))
    current_close = statement["saldo_corte"]
    next_open = next_statement["saldo_anterior"]
    difference = next_open - current_close
    excluded_invoices = [
        row
        for row in rows
        if row["record"].move_type == "out_invoice" and not row["included"]
    ]
    excluded_total = sum(row["amount"] for row in excluded_invoices)
    print("  Saldo al corte actual:       %s" % _money(currency, current_close))
    print("  Saldo anterior mes siguiente: %s" % _money(currency, next_open))
    print("  Diferencia:                  %s" % _money(currency, difference))
    print("  Facturas no clasificadas:    %s" % _money(currency, excluded_total))

    if _same_amount(currency, difference, excluded_total) and excluded_total:
        print(
            "CONCLUSION: la diferencia de continuidad coincide con las facturas "
            "del mes que entran al saldo historico, pero no se muestran como "
            "compras/cadis ni como otros cargos."
        )
    elif _same_amount(currency, difference, 0.0):
        print("CONCLUSION: el saldo al corte enlaza con el saldo del mes siguiente.")
    else:
        print(
            "CONCLUSION: la diferencia no se explica solo por facturas no "
            "clasificadas; revise tambien fechas efectivas, notas de credito y "
            "pagos listados arriba."
        )


def main(env):
    company = _company(env)
    partner = _partner(env, company)
    year = _integer_setting("STATEMENT_YEAR", 2026)
    month = _integer_setting("STATEMENT_MONTH", 6)
    if month not in range(1, 13):
        raise RuntimeError("STATEMENT_MONTH debe estar entre 1 y 12.")

    date_from, date_to = _period_dates(year, month)
    next_year, next_month = _next_period(year, month)
    wizard = _wizard(env, company, partner, year, month)
    next_wizard = _wizard(env, company, partner, next_year, next_month)
    statement = wizard._build_partner_statement(partner, date_from, date_to)
    next_from, next_to = _period_dates(next_year, next_month)
    next_statement = next_wizard._build_partner_statement(
        partner, next_from, next_to
    )
    rows = _period_analysis(wizard, partner, date_from, date_to)
    opening_lines, period_lines = _receivable_lines(
        wizard, partner, date_from, date_to
    )
    currency = company.currency_id

    _heading("Diagnostico de estado de cuenta Mayan (solo lectura)")
    print("Base de datos: %s" % env.cr.dbname)
    print("Compania: %s (ID %s)" % (company.display_name, company.id))
    print("Cliente: %s (ID %s)" % (partner.display_name, partner.id))
    print("Codigo: %s" % wizard._partner_code(partner))

    _heading("Resumen calculado por el reporte nuevo")
    _print_summary(currency, statement, date_from, date_to)
    _print_reference(currency, statement)
    _print_moves(currency, rows)
    _print_receivable_adjustments(
        currency,
        wizard,
        partner,
        date_from,
        date_to,
    )
    _print_payments(currency, wizard, partner, date_from, date_to)
    _print_receivable_ledger(currency, opening_lines, period_lines)
    _print_continuity(
        currency, statement, next_statement, rows, next_year, next_month
    )
    print("\nDiagnostico finalizado. No se modificaron registros.\n")


if "env" not in globals():
    raise RuntimeError(
        "Ejecute este archivo mediante 'odoo-bin shell -d NOMBRE_BD < ...'."
    )

main(env)
