import base64
import calendar
import re
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models, tools
from odoo.exceptions import AccessError, UserError, ValidationError


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
    email_log_ids = fields.Many2many(
        "mayan.customer.statement.email.log",
        string="Historial de correos",
        compute="_compute_email_history",
    )
    email_sent_count = fields.Integer(
        string="Enviados",
        compute="_compute_email_history",
    )
    email_queued_count = fields.Integer(
        string="En cola",
        compute="_compute_email_history",
    )
    email_failed_count = fields.Integer(
        string="Con error",
        compute="_compute_email_history",
    )

    @api.depends("company_id", "partner_ids", "year", "month")
    def _compute_email_history(self):
        EmailLog = self.env["mayan.customer.statement.email.log"]
        for wizard in self:
            logs = EmailLog.browse()
            if (
                wizard.company_id
                and wizard.partner_ids
                and wizard.year
                and wizard.month
            ):
                logs = EmailLog.search(
                    [
                        ("company_id", "=", wizard.company_id.id),
                        ("partner_id", "in", wizard.partner_ids.ids),
                        ("year", "=", wizard.year),
                        ("month", "=", wizard.month),
                    ],
                    order="create_date desc, id desc",
                    limit=500,
                )
            wizard.email_log_ids = logs
            wizard.email_sent_count = len(
                logs.filtered(lambda log: log.state == "sent")
            )
            wizard.email_queued_count = len(
                logs.filtered(lambda log: log.state == "queued")
            )
            wizard.email_failed_count = len(
                logs.filtered(lambda log: log.state == "failed")
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

    def action_send_statements(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_invoice"):
            raise AccessError(
                _("No tiene permisos para enviar estados de cuenta por correo.")
            )
        if not self.partner_ids:
            raise UserError(_("Seleccione al menos un cliente."))

        template = self.env.ref(
            "mayan_customer_statement.mail_template_customer_statement",
            raise_if_not_found=False,
        )
        if not template:
            raise UserError(
                _("No se encontró la plantilla de correo del estado de cuenta.")
            )

        EmailLog = self.env["mayan.customer.statement.email.log"]
        period_label = "%s %s" % (dict(MONTH_SELECTION)[self.month], self.year)
        email_from = (
            self.company_id.partner_id.email_formatted
            or self.env.user.email_formatted
            or False
        )
        partners = self.partner_ids.sorted(
            key=lambda partner: (partner.name or "", partner.id)
        )

        for partner in partners:
            recipient = self._statement_recipient(partner)
            subject = _("Estado de cuenta - %(partner)s - %(period)s") % {
                "partner": partner.name,
                "period": period_label,
            }
            log = EmailLog.create(
                {
                    "partner_id": partner.id,
                    "company_id": self.company_id.id,
                    "year": self.year,
                    "month": self.month,
                    "recipient": recipient or _("Sin correo configurado"),
                    "subject": subject,
                    "state": "queued",
                    "user_id": self.env.user.id,
                }
            )
            if not recipient:
                log.write(
                    {
                        "state": "failed",
                        "failure_reason": _(
                            "El socio no tiene una dirección de correo válida."
                        ),
                    }
                )
                continue

            try:
                result_values = {}
                with self.env.cr.savepoint():
                    individual_wizard = self.create(
                        {
                            "company_id": self.company_id.id,
                            "partner_ids": [(6, 0, [partner.id])],
                            "year": self.year,
                            "month": self.month,
                        }
                    )
                    pdf_content, output_format = self.env[
                        "ir.actions.report"
                    ]._render_qweb_pdf(
                        "mayan_customer_statement.action_customer_statement_report",
                        res_ids=individual_wizard.ids,
                    )
                    if output_format != "pdf":
                        raise UserError(
                            _("Odoo no pudo generar el estado de cuenta en PDF.")
                        )

                    filename = self._statement_filename(partner)
                    email_values = {
                        "email_to": recipient,
                        "recipient_ids": [(4, partner.id)],
                        "subject": subject,
                        "attachments": [
                            (filename, base64.b64encode(pdf_content))
                        ],
                        "auto_delete": False,
                    }
                    if email_from:
                        email_values["email_from"] = email_from

                    mail_id = (
                        template.with_company(self.company_id)
                        .with_context(lang=partner.lang or self.env.lang)
                        .send_mail(
                            partner.id,
                            force_send=True,
                            raise_exception=False,
                            email_values=email_values,
                        )
                    )
                    mail = self.env["mail.mail"].sudo().browse(mail_id).exists()
                    attachment = mail.attachment_ids.filtered(
                        lambda item: item.name == filename
                    )[:1]
                    if not attachment:
                        attachment = mail.attachment_ids[:1]
                    result_values = self._email_log_result_values(mail)
                    result_values.update(
                        {
                            "mail_id": mail.id if mail else False,
                            "attachment_id": attachment.id if attachment else False,
                        }
                    )
                    individual_wizard.unlink()
                log.write(result_values)
            except Exception as error:  # keep sending the remaining partners
                log.write(
                    {
                        "state": "failed",
                        "failure_reason": tools.ustr(error),
                    }
                )

        self.invalidate_recordset(
            [
                "email_log_ids",
                "email_sent_count",
                "email_queued_count",
                "email_failed_count",
            ]
        )
        action = self.env["ir.actions.actions"]._for_xml_id(
            "mayan_customer_statement.action_mayan_customer_statement_wizard"
        )
        action["res_id"] = self.id
        action["target"] = "new"
        return action

    @api.model
    def _statement_recipient(self, partner):
        emails = tools.email_split(partner.email or "")
        if not emails:
            emails = tools.email_split(partner.commercial_partner_id.email or "")
        return ", ".join(emails)

    def _statement_filename(self, partner):
        self.ensure_one()
        partner_label = self._partner_code(partner) or partner.name or str(partner.id)
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", partner_label).strip("_.")
        safe_label = safe_label or str(partner.id)
        return "Estado_de_cuenta_%s_%s_%02d.pdf" % (
            safe_label,
            self.year,
            int(self.month),
        )

    @api.model
    def _email_log_result_values(self, mail):
        if not mail:
            return {
                "state": "failed",
                "failure_reason": _("Odoo no creó el correo saliente."),
            }
        if mail.state in ("sent", "received"):
            return {
                "state": "sent",
                "sent_at": fields.Datetime.now(),
                "failure_reason": False,
            }
        if mail.state == "outgoing":
            return {
                "state": "queued",
                "failure_reason": False,
            }
        return {
            "state": "failed",
            "failure_reason": mail.failure_reason
            or _("El correo terminó con estado: %s")
            % (mail.state or _("desconocido")),
        }

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
        payments = self._get_payments(partner, date_to)
        receivable_adjustments = self._get_receivable_adjustments(
            partner,
            date_to,
            moves=moves,
            payments=payments,
        )
        previous_balance = self._previous_month_closing(
            partner,
            date_from,
            moves,
            payments,
            receivable_adjustments,
        )
        period = self._period_activity(
            partner,
            date_from,
            date_to,
            include_lines=True,
            moves=moves,
            payments=payments,
            receivable_adjustments=receivable_adjustments,
        )

        summary = self._compute_summary(
            previous_balance,
            period["purchases"],
            period["other_charges"],
            period["payments"],
        )
        lines = period["lines"]
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

    def _previous_month_closing(
        self,
        partner,
        date_from,
        moves,
        payments,
        receivable_adjustments,
    ):
        previous_date_from, previous_date_to = self._previous_period_dates(date_from)
        opening_date_to = previous_date_from - relativedelta(days=1)

        historical = self._period_activity(
            partner,
            False,
            opening_date_to,
            include_lines=False,
            moves=moves,
            payments=payments,
            receivable_adjustments=receivable_adjustments,
        )
        previous_opening = self._compute_summary(
            0.0,
            historical["purchases"],
            historical["other_charges"],
            historical["payments"],
        )["saldo_corte"]

        previous_period = self._period_activity(
            partner,
            previous_date_from,
            previous_date_to,
            include_lines=False,
            moves=moves,
            payments=payments,
            receivable_adjustments=receivable_adjustments,
        )
        return self._compute_summary(
            previous_opening,
            previous_period["purchases"],
            previous_period["other_charges"],
            previous_period["payments"],
        )["saldo_corte"]

    @api.model
    def _previous_period_dates(self, date_from):
        previous_date_to = date_from - relativedelta(days=1)
        return previous_date_to.replace(day=1), previous_date_to

    def _period_activity(
        self,
        partner,
        date_from,
        date_to,
        include_lines,
        moves=None,
        payments=None,
        receivable_adjustments=None,
    ):
        purchases = 0.0
        other_charges = 0.0
        payment_total = 0.0
        lines = []

        moves = moves if moves is not None else self._get_moves(partner, date_to)
        payment_records = (
            payments
            if payments is not None
            else self._get_payments(partner, date_to)
        )
        adjustment_lines = (
            receivable_adjustments
            if receivable_adjustments is not None
            else self._get_receivable_adjustments(
                partner,
                date_to,
                moves=moves,
                payments=payment_records,
            )
        )

        for move in moves:
            effective_date = self._move_effective_date(move)
            if (
                not effective_date
                or effective_date > date_to
                or (date_from and effective_date < date_from)
            ):
                continue

            amount = abs(move.amount_total_signed)
            category = self._statement_move_category(move)
            if category == "purchase":
                purchases += amount
                if include_lines:
                    lines.append(
                        self._move_line(move, effective_date, amount, 0.0, True)
                    )
            elif category == "other_charge":
                other_charges += amount
                if include_lines:
                    lines.append(
                        self._move_line(move, effective_date, amount, 0.0, False)
                    )
            elif category == "credit_note":
                payment_total += amount
                if include_lines:
                    lines.append(
                        self._move_line(move, effective_date, 0.0, amount, False)
                    )

        for payment in payment_records:
            if payment.date > date_to or (date_from and payment.date < date_from):
                continue
            amount = abs(payment.amount_company_currency_signed)
            payment_total += amount
            if include_lines:
                lines.append(self._payment_line(payment, amount))

        for line in adjustment_lines:
            if line.date > date_to or (date_from and line.date < date_from):
                continue
            if self.company_id.currency_id.is_zero(line.balance):
                continue

            amount = abs(line.balance)
            category = self._statement_receivable_category(line)
            if category == "purchase":
                purchases += amount
                if include_lines:
                    lines.append(
                        self._receivable_adjustment_line(line, amount, 0.0)
                    )
            elif category == "other_charge":
                other_charges += amount
                if include_lines:
                    lines.append(
                        self._receivable_adjustment_line(line, amount, 0.0)
                    )
            elif category == "credit":
                payment_total += amount
                if include_lines:
                    lines.append(
                        self._receivable_adjustment_line(line, 0.0, amount)
                    )

        lines.sort(key=lambda line: line["sort_key"])
        return {
            "purchases": purchases,
            "other_charges": other_charges,
            "payments": payment_total,
            "lines": lines,
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

    def _get_receivable_adjustments(
        self,
        partner,
        date_to,
        moves=None,
        payments=None,
    ):
        moves = moves if moves is not None else self._get_moves(partner, date_to)
        payments = (
            payments
            if payments is not None
            else self._get_payments(partner, date_to)
        )
        represented_move_ids = set(moves.ids)
        if "move_id" in payments._fields:
            represented_move_ids.update(payments.mapped("move_id").ids)

        domain = [
            ("company_id", "=", self.company_id.id),
            ("partner_id", "child_of", partner.commercial_partner_id.id),
            ("parent_state", "=", "posted"),
            ("move_id.move_type", "not in", ("out_invoice", "out_refund")),
            ("account_id.account_type", "=", "asset_receivable"),
            ("date", "<=", date_to),
        ]
        if represented_move_ids:
            domain.append(("move_id", "not in", list(represented_move_ids)))

        return (
            self.env["account.move.line"]
            .sudo()
            .with_company(self.company_id)
            .search(domain, order="date, move_name, id")
        )

    @api.model
    def _statement_move_category(self, move):
        if move.move_type == "out_refund":
            return "credit_note"
        if move.move_type == "out_invoice":
            return (
                "other_charge"
                if move.journal_id.mayan_otros_cargos
                else "purchase"
            )
        return False

    @api.model
    def _statement_receivable_category(self, line):
        if line.balance < 0:
            return "credit"
        if line.balance > 0:
            return (
                "other_charge"
                if line.move_id.journal_id.mayan_otros_cargos
                else "purchase"
            )
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
    def _receivable_adjustment_line(self, line, debit, credit):
        move = line.move_id
        return {
            "date": line.date,
            "date_label": line.date.strftime("%d/%m/%Y"),
            "document": move.name or "",
            "description": line.name or move.ref or _("Ajuste contable"),
            "debit": debit,
            "credit": credit,
            "sort_key": (line.date, move.name or "", line.id),
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
