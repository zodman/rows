# coding: utf-8

# Copyright 2014-2022 Álvaro Justen <https://github.com/turicas/rows/>
# Copyright 2022 João S. O. Bueno <https://github.com/jsbueno/>

#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Lesser General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Lesser General Public License for more details.

#    You should have received a copy of the GNU Lesser General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

import contextlib
import os
import warnings

from collections import namedtuple, OrderedDict
from operator import itemgetter
from pathlib import Path

from collections.abc import MutableSequence, Sized, Sequence, Mapping
from textwrap import dedent as D

from .utils import query
from .rows import CustomRowMixin


class BaseTable(MutableSequence, CustomRowMixin):

    _rows: Sequence


    def __init__(self, fields, meta=None, *, filter=None, **kwargs):
        from rows.fields import slug

        # Field order is guarranteed by Dictionaries preserving insetion order in Py 3.6+
        # (NB.: In Py 3.6 dict order is an "implementation detail", but from
        # 3.7 on it is a language spec.


        # Field names are automatically slugged in the internal repr.
        # Original names are stored as "str_fields"

        fields = dict(fields)
        self.str_field_names = list(fields.keys())

        self.fields = {
                slug(field_name): field_type
                for field_name, field_type in fields.items()
        }

        self.meta = dict(meta) if meta is not None else {}
        self.filter = filter
        super().__init__(**kwargs)


    def __len__(self):
        return len(self._rows)

    #@property
    #def Row(self):
        #"""Returns the class to be used to represent a row from this table.

            #by default, Rows are Python's namedtuples with the table field names.
            #For other objects, create a mixin replacing this method.
        #"""
        #if not getattr(self, "fields", None):
            #raise RuntimeError("Table must know its fields before being able to determine a Row class")
        #if not getattr(self, "_row_cls_namedtuple", None):
                #self._row_cls_namedtuple = namedtuple("Row", self.field_names)
        #return self._row_cls_namedtuple


    def _repr_html_(self):
        import rows.plugins

        HEAD_THRESHOLD = 20

        convert_to_html = rows.plugins.html.export_to_html

        total = len(self)
        if total <= HEAD_THRESHOLD:
            result = convert_to_html(self, caption=True)

        else:  # Show only head and tail
            representation = Table(
                fields=OrderedDict(
                    [
                        (field_name, rows.fields.TextField)
                        for field_name in self.field_names
                    ]
                ),
                meta={"name": self.name},
            )
            for row in self.head():
                representation.append(
                    {
                        field_name: field_type.serialize(getattr(row, field_name))
                        for field_name, field_type in self.fields.items()
                    }
                )
            representation.append(
                {field_name: "..." for field_name in self.field_names}
            )
            for row in self.tail():
                representation.append(
                    {
                        field_name: field_type.serialize(getattr(row, field_name))
                        for field_name, field_type in self.fields.items()
                    }
                )

            result = convert_to_html(representation, caption=True)
            result = result.replace(
                b"</caption>",
                f" (showing {HEAD_THRESHOLD} rows, out of {total})</caption>".encode()
            )
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        return result

    @property
    def field_names(self):
        return list(self.fields.keys())

    @property
    def field_types(self):
        return list(self.fields.values())

    @property
    def name(self):
        """Define table name based on its metadata (filename used on import)

        If `filename` is not available, return `table1`.
        """

        from rows.fields import slug

        name = self.meta.get("name", None)
        if name is not None:
            return slug(name)

        source = self.meta.get("source", None)
        if source and source.uri:
            return slug(os.path.splitext(Path(source.uri).name)[0])

        return "table1"

    def __repr__(self):
        length = len(self) if isinstance(self, Sized) else "?"

        imported = ""
        if "imported_from" in self.meta:
            imported = " (from {})".format(self.meta["imported_from"])

        return "<rows.Table{} {} fields, {} rows>".format(
            imported, len(self.fields), length
        )

    def _make_row(self, row: "Sequence|Mapping"):
        if isinstance(row, Sequence):
            if isinstance(row, (str, bytes)):
                raise TypeError()
            return [field_type.deserialize(row[i]) for i, field_type in enumerate(self.fields.values())]
        return [
            field_type.deserialize(row.get(field_name, None))
            for field_name, field_type in self.fields.items()
        ]

    def insert(self, index, row):
        self._rows.insert(index, self._make_row(row))

    def __radd__(self, other):
        if other == 0:
            return self
        raise ValueError()

    def __iadd__(self, other):
        return self + other

    def _stub_clone(self):
        return type(self)(self.fields.copy(), meta=self.meta.copy())

    @classmethod
    def copy(cls, table, data):
        """Creates a new table, copying the structure of the given table, and filling it witht the given data"""
        new = table._stub_clone()
        new.extend(data)
        return new

    def __add__(self, other):
        """Vertical concatenation of tables featuring the same field names.

        The leftmost table type, field types and metadata are used in the resulting table.
        """
        if isinstance(other, BaseTable) and self.field_names == other.field_names:
            new = self._stub_clone()
            new.extend(self._rows)
            new.extend(other._rows)
            return new

        return NotImplemented

    @property
    def filter(self):
        return self._filter

    @filter.setter
    def filter(self, filter):
        from rows.utils.query import Query, ensure_query
        if not isinstance(filter, Query):
            filter = ensure_query(filter)
        if filter and not filter.bound:
            # Query.bind consumes the class "filter_binding_type" attribute to know
            # how to render itself for use. Current values valid values are: anything truthfull
            # for expression resolvable filters, and "literal" to generate a string expression
            # which happens to be valid for a SQL "WHERE" clause
            filter = filter.bind(self)
        self._filter = filter
        if getattr(self, "filter_reset", None):
            self.filter_reset()

    @filter.deleter
    def filter(self):
        self._filter = None


class FilterableSequence(MutableSequence):
    """Inner sequence that actually applies a query filter row by row

    Few things in the Universe are as thread-unsafe as this;
    never try to use a filtered Table in more than one thread.
    """
    def __init__(self, inner, parent):
        self.data = inner
        self.parent = parent
        self._tick = 0
        self.invalidate()
        # uses parent.filter and parent.fields - TODO:  decouple that a bit

    def invalidate(self):
        self._row_map = {}
        self._finished_map = False
        self._tick += 1

    # tied to "per record filtering"
    def __iter__(self):
        current_tick = self._tick
        if not self.parent.filter:
            return iter(self.data)
        valid_rows_counter = 0
        for i, row in enumerate(self.data):
            self.parent.current_record = row if isinstance (row, Mapping) else {
                key: value for key, value in zip(self.parent.fields, row)}
            if self.parent.filter.value:
                self._row_map[valid_rows_counter] = i
                valid_rows_counter += 1
                yield row
        # Avoids that a iterator that has been paused, with
        # changes taking place in the pauses, marks
        # the rows as incorrectly filtered
        if current_tick == self._tick:
            self._finished_map = True


    def ensure_filtered(self):
        if not self._finished_map:
            # consume self.__iter__: updates self._rows_map
            for row in self:
                pass

    def __getitem__(self, index):
        self.ensure_filtered()
        try:
            return self.data[self._row_map[index]]
        except KeyError as e:
            raise IndexError from e

    def __setitem__(self, index, value):
        self.ensure_filtered()
        self.data[self._row_map[index]] = value
        self.invalidate()

    def __delitem__(self, index):
        self.ensure_filtered()
        del self.data[self._row_map[index]]
        self.invalidate()

    def __len__(self):
        if not self.parent.filter:
            return len(self.data)
        self.ensure_filtered()
        return len(self._row_map)

    def insert(self, position, row):
        """Inserting in a table ignores any filtering

        This behavior is needed because rows are inserted as part of collections.abc.MutableSequence protocol,
        which calls insert for every row, and we do this after the filter object is set if it
        is passed on table creation.
        """
        if self.parent.filter:
            warnings.warn(D("""\
                Inserting rows in a table with an active filter, will ignore the filter,
                and can result in quadratically slow workflows.

                Consider removing the filter for insertion - or using the "pause_filter()"
                context manager on the parent object.
                """))
        self.data.insert(position, row)
        self.invalidate()


class PerRecordFilterable(query.QueryableMixin, MutableSequence):

    # Has to inherit from MutableSequence so thatr this cls.extend have
    # priority over MutableSequence.extend when the mixin is used.

    filter = None

    @property
    def filtering_strategy(self):
        return self.current_record

    def filter_reset(self):
        self._inner_rows.invalidate()

    @property
    def _rows(self):
        if not self.filter:
            return self._inner_rows.data
        return self._inner_rows

    @_rows.setter
    def _rows(self, sequence):
        self._inner_rows=FilterableSequence(sequence, self)

    @contextlib.contextmanager
    def pause_filter(self):
        filter_ = getattr(self, "filter", None)
        try:
            self.filter = None
            yield
        finally:
            self.filter = filter_

    def extend(self, iterable):
        with self.pause_filter():
            super().extend(iterable)


class Table(BaseTable, PerRecordFilterable):
    def __init__(self, fields, meta=None, **kwargs):
        self._rows = []
        super().__init__(fields=fields, meta=meta, **kwargs)

    def head(self, n=10):
        return Table.copy(self, self._rows[:n])

    def tail(self, n=10):
        return Table.copy(self, self._rows[-n:])

    def __getitem__(self, key):
        """Retrives items from table

        Args:
            key: Union[int|slice|str] -> can be an integer index or a slice, which will select rows
                case int: returns a single row at position
                case slice: returns a new table with a  copy of given rows
                case str: returns a list containing the values of that column for all rows.

        To get table views, avoiding new tables and copies, use "Queries" (work in progress).

        """
        key_type = type(key)
        if key_type == int:
            return self._row_for_output(self._rows[key])
        elif key_type == slice:
            return Table.copy(self, self._rows[key])
        elif issubclass(key_type, str):
            try:
                field_index = self.field_names.index(key)
            except ValueError:
                raise KeyError(key)

            return [row[field_index] for row in self._rows]
        else:
            raise TypeError(f"Unsupported key type: {type(key).__name__}")

    def __setitem__(self, key, value):
        key_type = type(key)
        if key_type == int:
            self._rows[key] = self._make_row(value)
        elif issubclass(key_type, str):
            from rows import fields

            values = list(value)  # I'm not lazy, sorry
            if len(values) != len(self):
                raise ValueError(
                    "Values length ({}) should be the same as "
                    "Table length ({})".format(len(values), len(self))
                )

            field_name = fields.slug(key)
            is_new_field = field_name not in self.field_names
            field_type = fields.detect_types(
                [field_name], [[value] for value in values]
            )[field_name]
            self.fields[field_name] = field_type

            if is_new_field:
                for row, value in zip(self._rows, values):
                    row.append(field_type.deserialize(value))
            else:
                field_index = self.field_names.index(field_name)
                for row, value in zip(self._rows, values):
                    row[field_index] = field_type.deserialize(value)
        else:
            raise ValueError("Unsupported key type: {}".format(type(key).__name__))

    def __delitem__(self, key):
        key_type = type(key)
        if key_type == int:
            del self._rows[key]
        elif issubclass(key_type, str):
            try:
                field_index = self.field_names.index(key)
            except ValueError:
                raise KeyError(key)

            del self.fields[key]

            for row in self._rows:
                row.pop(field_index)
        else:
            raise ValueError("Unsupported key type: {}".format(type(key).__name__))

    def __add__(self, other):
        if other == 0:
            return self

        if not isinstance(self, type(other)) or self.fields != other.fields:
            raise ValueError("Tables have incompatible fields")
        else:
            table = Table(fields=self.fields)
            table._rows = self._rows + other._rows
            return table

    def order_by(self, key):
        # TODO: implement locale
        # TODO: implement for more than one key
        reverse = False
        if key.startswith("-"):
            key = key[1:]
            reverse = True

        field_names = self.field_names
        if key not in field_names:
            raise ValueError('Field "{}" does not exist'.format(key))

        key_index = field_names.index(key)
        self._rows.sort(key=itemgetter(key_index), reverse=reverse)


class FlexibleTable(Table):
    """ Table implementation featuring flexible columns: fields can be created on the go

    Rows are stored internally as dictionaries.

    Adding a new row with up to that point unknown fields, will create new fields
    for the table from that point on. Existing rows, when read, will feature the default
    'None' for columns that did not exist upon its insertion.
    """
    def __init__(self, fields=None, meta=None, **kwargs):
        if fields is None:
            fields = {}
        super(FlexibleTable, self).__init__(fields, meta=meta, **kwargs)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._row_for_output(self._rows[key])
        elif isinstance(key, slice):
            return [self._row_for_output(row) for row in self._rows[key]]
        else:
            raise ValueError("Unsupported key type: {}".format(type(key).__name__))

    def _add_field(self, field_name, field_type):
        self.fields[field_name] = field_type
        # cls.Row is lazily generated based on fields

    def _make_row(self, row):
        from rows import fields

        if isinstance(row, Mapping):
            for field_name in row.keys():
                if field_name not in self.field_names:
                    self._add_field(field_name, fields.identify_type(row[field_name]))

            return {
                field_name: field_type.deserialize(row.get(field_name, None))
                for field_name, field_type in self.fields.items()
            }
        return {
            field_name: field_type.deserialize(row[i])
            for i, (field_name, field_type) in enumerate(self.fields.items())
        }

    def __setitem__(self, index, value):
        self._rows[index] = self._make_row(value)


class SQLiteTable(BaseTable):
    filter_binding_type = "literal"


    def __init__(self, fields, meta=None, **kwargs):
        super(SQLiteTable, self).__init__(fields=fields, meta=meta, **kwargs)

        import sqlite3
        from rows.plugins.sqlite import create_table_sql
        self._connection = sqlite3.connect(":memory:")
        field_names = ["__id"] + self.field_names
        field_types = ["pkey"] + self.field_types
        self._execute(create_table_sql(self.name, field_names, field_types))

    def _execute(self, query, args=None, data_type="dict", many=False):
        if data_type not in ("dict", "list"):
            raise ValueError("data_type must be `dict` or `list`")

        cursor = self._connection.cursor()
        if not many:
            cursor.execute(query, args or [])
        else:
            cursor.executemany(query, args or [])
        header = [item[0] for item in cursor.description] if cursor.description else None
        try:
            if header is None:  # No results
                data = []
            else:
                data = cursor.fetchall()
        except Exception:
            # cant have an "else:" clause it there is no "except:" clause.
            raise
        else:
            self._connection.commit()
        finally:
            cursor.close()
        if data_type == "dict":
            data = [dict(zip(header, row)) for row in data]
        return data

    def _build_filtered_select(self, fields=None, offset=None, limit=None):
        from rows.plugins.sqlite import sqlite_escape_col, SQLiteOp
        # Use non string SQLiteOP instances to convey SQLITE functions or operations to be used in the SELECT clause
        escaped_fields = [field.value  if isinstance(field, SQLiteOp) else sqlite_escape_col(field) for field in fields or self.field_names]
        fields = ", ".join(escaped_fields)
        if not self.filter:
            where = ""
        else:
            where = f"WHERE {self.filter.value}"
        if limit is not None:
            limit = f"LIMIT {limit}"
        if offset is not None:
            offset = f"OFFSET {offset}"
        return f"SELECT {fields} FROM {self.name} {where} {limit or ''} {offset or ''}".strip()

    @classmethod
    def copy(cls, table, data):
        raise NotImplementedError()

    def head(self, n=10):
        raise NotImplementedError()

    def tail(self, n=10):
        raise NotImplementedError()

    def insert(self, index, row):
        # MutableSequence expects a working "insert" method.
        if index >= len(self):
            self.append(row)
            return
        raise NotImplementedError("Can't insert items in middle of SQLITE backed tables")

    def append(self, row):
        """Add a row to the table. Should be a dict"""

        # NB: this is a reversal of the given methods from MutableSequence,
        # which uses "insert" to implement "append", and "append" to implement "extend".

        self.extend([row])

    def extend(self, many_rows):
        """Add rows to the table. Should be a list of dicts"""

        from rows.plugins.sqlite import SQL_INSERT, _python_to_sqlite
        field_names = self.field_names
        insert_sql = SQL_INSERT.format(
            table_name=self.name,
            field_names=", ".join(field_names),
            placeholders=", ".join("?" for _ in field_names),
        )
        _convert_row = _python_to_sqlite(self.field_types)
        data = ([row[field_name if isinstance(row, Mapping) else i] for i, field_name in enumerate(field_names)] for row in many_rows)
        self._execute(insert_sql, args=map(_convert_row, data), many=True)

    def __iter__(self):
        for row in self._execute(self._build_filtered_select(), data_type="list"):
            yield _row_for_output(row)

    def __len__(self):
        from rows.plugins.sqlite import SQLiteOp
        query = self._build_filtered_select(fields=(SQLiteOp("COUNT(*) AS total"),))
        return self._execute(query)[0]["total"]

    def __getitem__(self, key):

        if isinstance(key, str):
            if key not in self.field_names:
                raise KeyError(key)
            query = self._build_query(fields=(key,))
            mode = "col"
            return [item[0] for item in self._execute(query, data_type="list")]
        if hasattr(key, "__index__"):
            offset = key.__index__()
            limit = 1
            mode = "single"
        elif issubclass(key, slice):
            if key.step not in {None, 1}:
                raise NotImplementedError(f"Only unit step for {self.__class__.__name__} slicing is implemented so far")
            if key.stop is None:
                limit = None
            else:
                limit = key.stop - (key.start or 0)
            offset = key.start or 0
            mode = "slice"
        else:
            raise ValueError("Unsupported key type: {}".format(type(key).__name__))

        if offset < 0 or limit < 0:
            raise NotImplementedError(f"Negative indices not yet implemented for {self.__class__.__name__}")

        # TODO: return filtered table copy on sliced get

        query = self._build_filtered_select(limit=limit, offset=offset)
        result = self._execute(query, data_type="dict")

        return self.Row(result[0]) if mode == "single" else result if mode == "slice" else [item[0][key] for item in result]


    def __setitem__(self, key, value):
        raise NotImplementedError()

    def __delitem__(self, key):
        raise NotImplementedError()

    def __add__(self, other):
        raise NotImplementedError()

    def order_by(self, key):
        reverse = False
        if key.startswith("-"):
            key = key[1:]
            reverse = True

        if key not in self.field_names:
            raise ValueError('Field "{}" does not exist'.format(key))

        # TODO: use method to re-order __id
        raise NotImplementedError()

    def save(self, filename):
        import sqlite3

        conn = sqlite3.connect(filename)
        self._connection.backup(conn)
        conn.close()

    @classmethod
    def load(cls, filename):
        raise NotImplementedError()
