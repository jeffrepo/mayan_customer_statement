import calendar
import unicodedata
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


MONTH_SELECTION = [
    ("1", "Enero"),
    ("2", "Febrero"),
    ("3", "Marzo"),
    ("4", "Abril"),
    ("5", "Mayo"),
    ("6", "Junio"),
    ("7", "Julio"),
    ("8", "Agosto"),
    ("9", "Septiembre"),
    ("10", "Octubre"),
    ("11", "Noviembre"),
    ("12", "Diciembre"),
]

PAYMENT_METHOD_FIELD_CANDIDATES = (
    "identificar_cliente",
    "identify_customer",
    "identifica_cliente",
    "is_customer_identification",
)

FALLBACK_CREDIT_METHOD_NAMES = {
    "CREDITO RES",
    "CREDITO PIS",
    "CREDITO RAN",
    "CREDITO SERVICIOS",
}


class MayanCustomerStatementWizard(models.TransientModel):
    _name = "mayan.customer.statement.wizard"
    _description = "Estado de cuenta de clientes Mayan"

    @api.model
    def _default_period(self):
        today = fields.Date.context_today(self)
        return today.replace(day=1) - relativedelta(months=1)

    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        required=True,
        default=lambda self: self.env.company,
    )
    partner_ids = fields.Many2many(
        "res.partner",
        "mayan_customer_statement_wizard_partner_rel",
        "wizard_id",
        "partner_id",
        string="Clientes",
        required=True,
        domain=[("customer_rank", ">", 0)],
    )
    year = fields.Integer(
        string="Año",
        required=True,
        default=lambda self: self._default_period().year,
    )
    month = fields.Selection(
        MONTH_SELECTION,
        string="Mes",
        required=True,
        default=lambda self: str(self._default_period().month),
    )

    @api.constrains("year")
    def _check_year(self):
        for wizard in self:
            if not 2000 <= wizard.year <= 2100:
                raise ValidationError(_("El año debe estar entre 2000 y 2100."))

    def action_print(self):
        self.ensure_one()
        if not self.partner_ids:
            raise UserError(_("Seleccione al menos un cliente."))
        return self.env.ref(
            "mayan_customer_statement.action_customer_statement_report"
        ).report_action(self)

    @api.model
    def _compute_summary(self, previous_balance, purchases, other_charges, payments):
        subtotal = purchases + other_charges
        return {
            "saldo_anterior": previous_balance,
            "compras_cadis": purchases,
            "otros_cargos": other_charges,
            "subtotal_cargos": subtotal,
            "pagos": payments,
            "saldo_corte": previous_balance + subtotal - payments,
        }

    def _build_statements(self):
        self.ensure_one()
        date_from, date_to = self._period_dates()
        partners = self.partner_ids.sorted(
            key=lambda partner: (
                self._partner_code(partner) or "",
                partner.name or "",
                partner.id,
            )
        )
        return [
            self._build_partner_statement(partner, date_from, date_to)
            for partner in partners
        ]

    def _period_dates(self):
        month = int(self.month)
        last_day = calendar.monthrange(self.year, month)[1]
        return date(self.year, month, 1), date(self.year, month, last_day)

    def _build_partner_statement(self, partner, date_from, date_to):
        moves = self._get_moves(partner, date_to)
        pos_orders_by_move = self._get_pos_orders_by_move(moves)
        previous_balance = 0.0
        purchases = 0.0
        other_charges = 0.0
        payments = 0.0
        lines = []
        identify_field = self._identify_customer_field()

        for move in moves:
            effective_date = self._move_effective_date(move)
            if not effective_date:
                continue
            amount = abs(move.amount_total_signed)
            pos_orders = pos_orders_by_move.get(move.id, self.env["pos.order"])
            is_purchase = (
                move.move_type == "out_invoice"
                and bool(pos_orders)
                and any(
                    self._pos_order_is_statement_purchase(order, identify_field)
                    for order in pos_orders
                )
            )
            category = self._statement_move_category(
                move.move_type,
                is_purchase,
            )

            if effective_date < date_from:
                # El saldo inicial debe arrastrar toda la cuenta por cobrar del
                # cliente, no solo las dos categorías mostradas en el mes. Los
                # pagos históricos también abarcan la cuenta completa; excluir
                # cuotas u otras facturas generaría un saldo negativo artificial.
                if move.move_type == "out_invoice":
                    previous_balance += amount
                elif move.move_type == "out_refund":
                    previous_balance -= amount
                continue

            if category == "purchase":
                purchases += amount
                lines.append(self._move_line(move, effective_date, amount, 0.0, True))
            elif category == "other_charge":
                other_charges += amount
                lines.append(self._move_line(move, effective_date, amount, 0.0, False))
            elif category == "credit_note":
                payments += amount
                lines.append(self._move_line(move, effective_date, 0.0, amount, False))

        for payment in self._get_payments(partner, date_to):
            amount = abs(payment.amount_company_currency_signed)
            if payment.date < date_from:
                previous_balance -= amount
                continue
            payments += amount
            lines.append(self._payment_line(payment, amount))

        summary = self._compute_summary(
            previous_balance,
            purchases,
            other_charges,
            payments,
        )
        lines.sort(key=lambda line: line["sort_key"])
        bank = self.company_id.partner_id.bank_ids[:1]
        return {
            **summary,
            "company": self.company_id,
            "company_address": self._address(self.company_id.partner_id),
            "bank_name": self._bank_name(bank),
            "partner": partner,
            "partner_address": self._address(partner),
            "partner_code": self._partner_code(partner),
            "period_label": "%s %s" % (dict(MONTH_SELECTION)[self.month], self.year),
            "currency": self.company_id.currency_id,
            "lines": lines,
            "total_debit": sum(line["debit"] for line in lines),
            "total_credit": sum(line["credit"] for line in lines),
            "generated_on": fields.Date.context_today(self).strftime("%d/%m/%Y"),
        }

    def _get_moves(self, partner, date_to):
        Move = self.env["account.move"].sudo().with_company(self.company_id)
        domain = [
            ("company_id", "=", self.company_id.id),
            ("commercial_partner_id", "=", partner.commercial_partner_id.id),
            ("state", "=", "posted"),
            ("move_type", "in", ("out_invoice", "out_refund")),
        ]
        if "fecha_estado_cuenta" in Move._fields:
            domain += [
                "|",
                ("fecha_estado_cuenta", "<=", date_to),
                "&",
                ("fecha_estado_cuenta", "=", False),
                ("invoice_date", "<=", date_to),
            ]
        else:
            domain.append(("invoice_date", "<=", date_to))
        return Move.search(domain, order="invoice_date, name, id")

    def _get_payments(self, partner, date_to):
        return self.env["account.payment"].sudo().with_company(self.company_id).search(
            [
                ("company_id", "=", self.company_id.id),
                ("partner_id", "child_of", partner.commercial_partner_id.id),
                ("partner_type", "=", "customer"),
                ("payment_type", "=", "inbound"),
                ("state", "in", ("in_process", "paid")),
                ("date", "<=", date_to),
            ],
            order="date, name, id",
        )

    def _get_pos_orders_by_move(self, moves):
        result = {}
        orders = self.env["pos.order"].sudo().search(
            [("account_move", "in", moves.ids)]
        )
        for order in orders:
            result.setdefault(order.account_move.id, orders.browse())
            result[order.account_move.id] |= order
        return result

    def _pos_order_is_statement_purchase(self, order, identify_field):
        payment_methods = order.payment_ids.payment_method_id
        if identify_field:
            return any(method[identify_field] for method in payment_methods)
        return any(
            self._normalize(method.name) in FALLBACK_CREDIT_METHOD_NAMES
            for method in payment_methods
        )

    @api.model
    def _identify_customer_field(self):
        PaymentMethod = self.env["pos.payment.method"]
        for field_name in PAYMENT_METHOD_FIELD_CANDIDATES:
            field = PaymentMethod._fields.get(field_name)
            if field and field.type == "boolean":
                return field_name
        for field_name, field in PaymentMethod._fields.items():
            if field.type != "boolean":
                continue
            label = self._normalize(field.string)
            if (
                ("IDENTIFIC" in label and "CLIENTE" in label)
                or ("IDENTIFY" in label and "CUSTOMER" in label)
            ):
                return field_name
        return False

    @api.model
    def _statement_move_category(self, move_type, is_purchase):
        if move_type == "out_refund":
            return "credit_note"
        if move_type == "out_invoice":
            return "purchase" if is_purchase else "other_charge"
        return False

    def _move_line(self, move, effective_date, debit, credit, use_fel):
        return {
            "date": effective_date,
            "date_label": effective_date.strftime("%d/%m/%Y"),
            "document": self._move_document(move, use_fel),
            "description": move.ref or "",
            "debit": debit,
            "credit": credit,
            "sort_key": (effective_date, move.name or "", move.id),
        }

    def _payment_line(self, payment, amount):
        document = ""
        if "numero_recibo" in payment._fields:
            document = payment.numero_recibo or ""
        document = document or payment.payment_reference or payment.name or ""
        return {
            "date": payment.date,
            "date_label": payment.date.strftime("%d/%m/%Y"),
            "document": document,
            "description": payment.memo or _("Pago recibido"),
            "debit": 0.0,
            "credit": amount,
            "sort_key": (payment.date, payment.name or "", payment.id),
        }

    @api.model
    def _move_effective_date(self, move):
        if "fecha_estado_cuenta" in move._fields and move.fecha_estado_cuenta:
            return fields.Date.to_date(move.fecha_estado_cuenta)
        return move.invoice_date or move.date

    @api.model
    def _move_document(self, move, use_fel):
        if use_fel:
            series = move.fac_serie if "fac_serie" in move._fields else False
            number = move.fac_numero if "fac_numero" in move._fields else False
            if series and number:
                return "%s-%s" % (series, number)
            if series or number:
                return str(series or number)
        return move.name or ""

    @api.model
    def _partner_code(self, partner):
        if "codigo_socio" in partner._fields and partner.codigo_socio:
            return str(partner.codigo_socio)
        return str(partner.ref or "")

    @api.model
    def _address(self, partner):
        values = [
            partner.street,
            partner.street2,
            " ".join(filter(None, [partner.zip, partner.city])),
            partner.state_id.name,
            partner.country_id.name,
        ]
        return ", ".join(value for value in values if value)

    @api.model
    def _bank_name(self, bank):
        if not bank:
            return ""
        label = bank.bank_id.name or ""
        if bank.acc_number:
            label = "%s - %s" % (label, bank.acc_number) if label else bank.acc_number
        return label

    @api.model
    def _normalize(self, value):
        value = value or ""
        normalized = unicodedata.normalize("NFKD", str(value))
        return " ".join(
            "".join(character for character in normalized if not unicodedata.combining(character))
            .upper()
            .split()
        )
