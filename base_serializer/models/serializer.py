# © 2022 Florian Kantelberg - initOS GmbH
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import json
import logging
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import safe_eval

_logger = logging.getLogger(__name__)


class Serializer(models.Model):
    _name = "ir.serializer"
    _description = _("Serializer")

    def _get_default_code(self, mode):
        variables = self.default_variables(mode)
        desc = "\n".join(f"# - {v}: {desc}" for v, desc in variables.items())
        return f"# Possible variables:\n{desc}\n\n"

    name = fields.Char(required=True, copy=False)
    exporting = fields.Boolean(default=True)
    importing = fields.Boolean(default=True)
    active = fields.Boolean(compute="_compute_active", store=True)
    model_id = fields.Many2one("ir.model", ondelete="cascade", required=True)
    field_ids = fields.One2many("ir.serializer.field", "serializer_id")
    use_snippet = fields.Boolean(
        help="Apply the python snippet after the field mapping",
    )
    use_sync_date = fields.Boolean(help="Always add a `sync_date` field")
    export_code = fields.Text(
        default=lambda self: self._get_default_code("export"),
        help="This snippet is additionally used to serialize the content",
    )
    export_domain = fields.Text(
        default="[]",
        help="This domains is used to filter the records which needs to be "
        "serialized",
    )
    import_code = fields.Text(
        default=lambda self: self._get_default_code("import"),
        help="This snippet is additionally used to deserialize the content",
    )
    import_domain = fields.Text(
        default="[('id', '=', id)]",
        help="During the import of deserialized data this domain is used to find "
        "the matching record. All mapped fields of the model can be used as variables",
    )
    import_create = fields.Selection(
        [("create", _("Create")), ("skip", _("Skip"), ("exception", _("Exception")))],
        help="Create records on import",
    )
    base_serializer_id = fields.Many2one(
        "ir.serializer",
        help="Base this serializer on another one to reduce redundancy",
    )
    raise_on_duplicate = fields.Boolean(
        default=True,
        help="Raise an error if a duplicate entry would be exported. This usually "
        "happens if you have a loop in the related fields. Otherwise the duplicate "
        "will be silently skipped",
    )
    include_empty_keys = fields.Boolean(
        default=False,
        help="If used empty dictionaries are used if a related field points to "
        "nothing otherwise the value is skipped",
    )

    _sql_constraints = [
        ("name_uniq", "UNIQUE(name)", _("The name must be unique")),
    ]

    @api.constrains("field_ids")
    def _check_fields(self):
        for rec in self:
            names = set()
            for field in rec.field_ids:
                if field.name and field.name in names:
                    raise ValidationError(_("Fields must be defined uniquely"))

                if not field.name and field.field_id.name in names:
                    raise ValidationError(_("Fields must be defined uniquely"))

                if field.name:
                    names.add(field.name)
                else:
                    names.add(field.field_id.name)

    @api.constrains("export_domain")
    def _check_filter(self):
        for rec in self:
            if not rec.export_domain:
                continue

            try:
                domain = rec._get_export_domain()
            except Exception as e:
                _logger.exception(e)
                raise ValidationError(
                    _("Invalid filter. See the following error:\n%s") % e
                ) from e

            if not isinstance(domain, (list, tuple)):
                raise ValidationError(_("Invalid domain"))

    @api.constrains("import_domain")
    def _check_domain(self):
        fields = self.env["ir.model.fields"]
        for rec in self:
            if not rec.import_domain:
                continue

            val = dict.fromkeys(
                fields.search([("model_id", "=", rec.model_id.id)]).mapped("name"),
                None,
            )
            try:
                domain = rec._get_import_domain(val)
            except Exception as e:
                _logger.exception(e)
                raise ValidationError(
                    _("Invalid domain. See the following error:\n%s") % e
                ) from e

            if not isinstance(domain, (list, tuple)):
                raise ValidationError(_("Invalid domain"))

    @api.depends("exporting", "importing")
    def _compute_active(self):
        for rec in self:
            rec.active = rec.importing or rec.exporting

    @api.model
    def default_variables(self, mode):
        """Informations about the available variables in the python code"""
        variables = {
            "env": "Odoo Environment on which the processing is triggered",
            "datetime, time": "useful Python libraries",
            "UserError": "Warning Exception to use with raise",
        }

        if mode == "export":
            variables.update(
                {
                    "record": "The record to process",
                    "result": "Result dictionary which will be converted to JSON",
                }
            )
        elif mode == "import":
            variables.update(
                {
                    "content": "Dictionary which should be deserialized",
                    "result": "Result dictionary which can be imported",
                }
            )

        return variables

    def _get_eval_context(self):
        self.ensure_one()
        return {
            "datetime": safe_eval.datetime,
            "env": self.env,
            "time": safe_eval.time,
            "UserError": UserError,
        }

    def _get_filter_context(self):
        self.ensure_one()
        return {
            "datetime": safe_eval.datetime,
            "ref": self._get_id,
            "time": safe_eval.time,
        }

    def _get_id(self, xmlid, raise_if_not_found=True):
        rec = self.env.ref(xmlid, raise_if_not_found)
        return rec.id if rec else False

    def _get_export_domain(self):
        self.ensure_one()
        return safe_eval.safe_eval(self.export_domain, self._get_filter_context())

    def _get_import_domain(self, values):
        self.ensure_one()
        return safe_eval.safe_eval(self.import_domain, values)

    def action_populate(self):
        self._populate()

    def action_populate_fully(self):
        self._populate(True)

    def _populate(self, fully=False):
        self.ensure_one()

        fields = self.mapped("field_ids.field_id")
        domain = [
            ("id", "not in", fields.ids),
            ("model_id", "=", self.model_id.id),
            ("store", "=", True),
        ]
        if not fully:
            domain.append(("relation", "in", (False, "")))

        self.field_ids = [
            (0, 0, {"field_id": field.id}) for field in fields.search(domain)
        ]

    def deserialize(self, content, jsonified=True):
        if jsonified:
            content = json.loads(content)

        if not isinstance(content, (list, tuple)):
            raise UserError(_("Expected a list of dictionaries"))

        if not all(isinstance(x, dict) for x in content):
            raise UserError(_("Expected a list of dictionaries"))

        return list(map(self._deserialize, content))

    def _deserialize(self, content):
        self.ensure_one()

        if not isinstance(content, dict):
            raise ValidationError(_("Expected a dictionary but got %s", content))

        if self.base_serializer_id:
            result = self.base_serializer_id._deserialize(content)
        else:
            result = {}

        for key, value in content.items():
            domain = [
                ("name", "=", key),
                ("importing", "=", True),
            ]
            field = self.field_ids.filtered_domain(domain)
            if not field:
                domain = [
                    ("name", "=", False),
                    ("field_id.name", "=", key),
                    ("importing", "=", True),
                ]
                field = self.field_ids.filtered_domain(domain)

            if not field:
                continue

            result[field.field_id.name] = field._deserialize(value)

        if self.use_snippet and self.import_code:
            ctx = self._get_eval_context()
            ctx.update({"content": content, "result": result})
            safe_eval.safe_eval(self.import_code, ctx, mode="exec", nocopy=True)

        if self.use_sync_date and "sync_date" in content:
            result["sync_date"] = datetime.fromisoformat(content["sync_date"])

        return result

    def import_deserialized(self, content):
        # Hint: skip_no_create stays for inheritance and resets with relations

        self.ensure_one()
        if not isinstance(content, (list, tuple)):
            content = [content]

        if self.base_serializer_id:
            related = self.base_serializer_id
            if self.env.context.get("skip_no_create") is None:
                related = related.with_context(skip_no_create=self.import_create)

            records = related.import_deserialized(content)
        else:
            records = self.env[self.model_id.model]

        for values in content:
            domain = self._get_import_domain(values)
            rec = self.env[self.model_id.model].search(domain)
            if rec:
                writable = self._import_deserialized(values)
                rec.write(writable)
            elif self.import_create == "create":
                writable = self._import_deserialized(values)
                rec = rec.create(writable)
            elif self.env.context.get("skip_no_create") == "exception":
                _logger.error(f"Import not allowed {self}: {content}")
                raise ValidationError(_("No matching record found"))
            records |= rec

        return records

    def _import_deserialized(self, content):
        self.ensure_one()
        if not isinstance(content, dict):
            raise UserError(_("Expected a dictionary but got %s", content))

        writable = {}
        for key, value in content.items():
            if not isinstance(value, (dict, list, tuple)):
                writable[key] = value
                continue

            domain = [("field_id.name", "=", key), ("importing", "=", True)]
            field = self.field_ids.filtered_domain(domain)
            if not field:
                continue

            if field.ttype == "many2one":
                related = field.related_serializer_id.with_context(skip_no_create=None)
                writable[key] = related.import_deserialized(value).id
            elif field.ttype in ("many2many", "one2many"):
                changes = [(5,)]
                related = field.related_serializer_id.with_context(skip_no_create=None)
                for val in value:
                    rec = related.import_deserialized(val)
                    if rec:
                        changes.append((4, rec.id))
                    elif self.import_create == "create":
                        rec_vals = related._import_deserialized(val)
                        changes.append((0, 0, rec_vals))
                    elif related.import_create == "exception":
                        _logger.error(f"Import not allowed {self}: {content}")
                        raise ValidationError(_("No matching record found"))

                writable[key] = changes
            else:
                raise NotImplementedError(
                    f"Field {key} of type {field.ttype} is not supported"
                )

        return writable

    def serialize(self, records, jsonify=True):
        self.ensure_one()
        self_ctx = self.with_context(
            include_empty_keys=self.include_empty_keys,
            raise_on_duplicate=self.raise_on_duplicate,
        )

        if self.export_domain:
            records = records.filtered_domain(self._get_export_domain())

        data = list(map(self_ctx._serialize, records))
        return json.dumps(data) if jsonify else data

    def _serialize(self, record, visited=None):
        self.ensure_one()

        if not record:
            return {} if self.env.context.get("include_empty_keys") else None

        record.ensure_one()
        if not visited:
            visited = []

        if (record, self) in visited:
            if self.env.context.get("raise_on_duplicate"):
                raise UserError(_("Loop detected"))
            return {} if self.env.context.get("include_empty_keys") else None

        visited.append((record, self))

        if self.base_serializer_id:
            result = self.base_serializer_id._serialize(record, visited) or {}
        else:
            result = {}

        for field in self.field_ids.filtered("exporting"):
            fname = field.name or field.field_id.name
            data = field._serialize(record, visited[:])
            if data is not None:
                result[fname] = field._serialize(record, visited[:])

        if self.use_snippet and self.export_code:
            ctx = self._get_eval_context()
            ctx.update({"result": result, "record": record})
            safe_eval.safe_eval(self.export_code, ctx, mode="exec", nocopy=True)

        if self.use_sync_date:
            result["sync_date"] = record.write_date.isoformat(" ")

        return result
