# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	validate_filters(filters)
	(
		tds_docs,
		tds_accounts,
		tax_category_map,
		journal_entry_party_map,
		invoice_net_total_map,
	) = get_tds_docs(filters)

	columns = get_columns(filters)

	res = get_result(
		filters, tds_docs, tds_accounts, tax_category_map, journal_entry_party_map, invoice_net_total_map
	)
	return columns, res


def validate_filters(filters):
	"""Validate if dates are properly set"""
	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date must be before To Date"))


def get_result(
	filters, tds_docs, tds_accounts, tax_category_map, journal_entry_party_map, invoice_net_total_map
):
	supplier_map = get_supplier_pan_map()
	tax_rate_map = get_tax_rate_map(filters)
	gle_map = get_gle_map(tds_docs)

	out = []
	for name, details in gle_map.items():
		tds_deducted, total_amount_credited = 0, 0
		tax_withholding_category = tax_category_map.get(name)
		rate = tax_rate_map.get(tax_withholding_category)

		for entry in details:
			supplier = entry.party or entry.against
			posting_date = entry.posting_date
			voucher_type = entry.voucher_type

			if voucher_type == "Journal Entry":
				suppliers = journal_entry_party_map.get(name)
				if suppliers:
					supplier = suppliers[0]

			if not tax_withholding_category:
				tax_withholding_category = supplier_map.get(supplier, {}).get("tax_withholding_category")
				rate = tax_rate_map.get(tax_withholding_category)

			if entry.account in tds_accounts:
				tds_deducted += entry.credit - entry.debit

			if invoice_net_total_map.get(name):
				total_amount_credited = invoice_net_total_map.get(name)
			else:
				total_amount_credited += entry.credit

		## Check if ldc is applied and show rate as per ldc
		actual_rate = (tds_deducted / total_amount_credited) * 100

		if flt(actual_rate) < flt(rate):
			rate = actual_rate

		if tds_deducted:
			row = {
				"pan"
				if frappe.db.has_column("Supplier", "pan")
				else "tax_id": supplier_map.get(supplier, {}).get("pan"),
				"supplier": supplier_map.get(supplier, {}).get("name"),
			}

			if filters.naming_series == "Naming Series":
				row.update({"supplier_name": supplier_map.get(supplier, {}).get("supplier_name")})

			row.update(
				{
					"section_code": tax_withholding_category,
					"entity_type": supplier_map.get(supplier, {}).get("supplier_type"),
					"tds_rate": rate,
					"total_amount_credited": total_amount_credited,
					"tds_deducted": tds_deducted,
					"transaction_date": posting_date,
					"transaction_type": voucher_type,
					"ref_no": name,
				}
			)

			out.append(row)

	return out


def get_supplier_pan_map():
	supplier_map = frappe._dict()
	suppliers = frappe.db.get_all(
		"Supplier", fields=["name", "pan", "supplier_type", "supplier_name", "tax_withholding_category"]
	)

	for d in suppliers:
		supplier_map[d.name] = d

	return supplier_map


def get_gle_map(documents):
	# create gle_map of the form
	# {"purchase_invoice": list of dict of all gle created for this invoice}
	gle_map = {}

	gle = frappe.db.get_all(
		"GL Entry",
		{"voucher_no": ["in", documents], "is_cancelled": 0},
		["credit", "debit", "account", "voucher_no", "posting_date", "voucher_type", "against", "party"],
	)

	for d in gle:
		if not d.voucher_no in gle_map:
			gle_map[d.voucher_no] = [d]
		else:
			gle_map[d.voucher_no].append(d)

	return gle_map


def get_columns(filters):
	pan = "pan" if frappe.db.has_column("Supplier", "pan") else "tax_id"
	columns = [
		{"label": _(frappe.unscrub(pan)), "fieldname": pan, "fieldtype": "Data", "width": 90},
		{
			"label": _("Supplier"),
			"options": "Supplier",
			"fieldname": "supplier",
			"fieldtype": "Link",
			"width": 180,
		},
	]

	if filters.naming_series == "Naming Series":
		columns.append(
			{"label": _("Supplier Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 180}
		)

	columns.extend(
		[
			{
				"label": _("Section Code"),
				"options": "Tax Withholding Category",
				"fieldname": "section_code",
				"fieldtype": "Link",
				"width": 180,
			},
			{"label": _("Entity Type"), "fieldname": "entity_type", "fieldtype": "Data", "width": 180},
			{"label": _("TDS Rate %"), "fieldname": "tds_rate", "fieldtype": "Percent", "width": 90},
			{
				"label": _("Total Amount Credited"),
				"fieldname": "total_amount_credited",
				"fieldtype": "Float",
				"width": 90,
			},
			{
				"label": _("Amount of TDS Deducted"),
				"fieldname": "tds_deducted",
				"fieldtype": "Float",
				"width": 90,
			},
			{
				"label": _("Date of Transaction"),
				"fieldname": "transaction_date",
				"fieldtype": "Date",
				"width": 90,
			},
			{"label": _("Transaction Type"), "fieldname": "transaction_type", "width": 90},
			{
				"label": _("Reference No."),
				"fieldname": "ref_no",
				"fieldtype": "Dynamic Link",
				"options": "transaction_type",
				"width": 90,
			},
		]
	)

	return columns


def get_tds_docs(filters):
	tds_documents = []
	purchase_invoices = []
	payment_entries = []
	journal_entries = []
	tax_category_map = frappe._dict()
	invoice_net_total_map = frappe._dict()
	or_filters = frappe._dict()
	journal_entry_party_map = frappe._dict()
	bank_accounts = frappe.get_all("Account", {"is_group": 0, "account_type": "Bank"}, pluck="name")

	tds_accounts = frappe.get_all(
		"Tax Withholding Account", {"company": filters.get("company")}, pluck="account"
	)

	query_filters = {
		"account": ("in", tds_accounts),
		"posting_date": ("between", [filters.get("from_date"), filters.get("to_date")]),
		"is_cancelled": 0,
		"against": ("not in", bank_accounts),
	}

	if filters.get("supplier"):
		del query_filters["account"]
		del query_filters["against"]
		or_filters = {"against": filters.get("supplier"), "party": filters.get("supplier")}

	tds_docs = frappe.get_all(
		"GL Entry",
		filters=query_filters,
		or_filters=or_filters,
		fields=["voucher_no", "voucher_type", "against", "party"],
	)

	for d in tds_docs:
		if d.voucher_type == "Purchase Invoice":
			purchase_invoices.append(d.voucher_no)
		elif d.voucher_type == "Payment Entry":
			payment_entries.append(d.voucher_no)
		elif d.voucher_type == "Journal Entry":
			journal_entries.append(d.voucher_no)

		tds_documents.append(d.voucher_no)

	if purchase_invoices:
		get_doc_info(purchase_invoices, "Purchase Invoice", tax_category_map, invoice_net_total_map)

	if payment_entries:
		get_doc_info(payment_entries, "Payment Entry", tax_category_map)

	if journal_entries:
		journal_entry_party_map = get_journal_entry_party_map(journal_entries)
		get_doc_info(journal_entries, "Journal Entry", tax_category_map)

	return (
		tds_documents,
		tds_accounts,
		tax_category_map,
		journal_entry_party_map,
		invoice_net_total_map,
	)


def get_journal_entry_party_map(journal_entries):
	journal_entry_party_map = {}
	for d in frappe.db.get_all(
		"Journal Entry Account",
		{"parent": ("in", journal_entries), "party_type": "Supplier", "party": ("is", "set")},
		["parent", "party"],
	):
		if d.parent not in journal_entry_party_map:
			journal_entry_party_map[d.parent] = []
		journal_entry_party_map[d.parent].append(d.party)

	return journal_entry_party_map


def get_doc_info(vouchers, doctype, tax_category_map, invoice_net_total_map=None):
	if doctype == "Purchase Invoice":
		fields = ["name", "tax_withholding_category", "base_tax_withholding_net_total"]
	else:
		fields = ["name", "tax_withholding_category"]

	entries = frappe.get_all(doctype, filters={"name": ("in", vouchers)}, fields=fields)

	for entry in entries:
		tax_category_map.update({entry.name: entry.tax_withholding_category})
		if doctype == "Purchase Invoice":
			invoice_net_total_map.update({entry.name: entry.base_tax_withholding_net_total})


def get_tax_rate_map(filters):
	rate_map = frappe.get_all(
		"Tax Withholding Rate",
		filters={
			"from_date": ("<=", filters.get("from_date")),
			"to_date": (">=", filters.get("to_date")),
		},
		fields=["parent", "tax_withholding_rate"],
		as_list=1,
	)

	return frappe._dict(rate_map)
