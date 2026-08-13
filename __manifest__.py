{
    "name": "Estado de cuenta de clientes - Mayan Golf",
    "summary": "Estado de cuenta mensual de clientes con consumos POS y recargos",
    "version": "18.0.1.0.1",
    "category": "Accounting/Accounting",
    "license": "LGPL-3",
    "depends": [
        "account",
        "account_debit_note",
        "point_of_sale",
        "mayangolf",
        "l10n_gt_sat",
    ],
    "data": [
        "security/ir.model.access.csv",
        "report/customer_statement_reports.xml",
        "report/customer_statement_templates.xml",
        "wizard/customer_statement_wizard_views.xml",
    ],
    "application": True,
    "installable": True,
}
