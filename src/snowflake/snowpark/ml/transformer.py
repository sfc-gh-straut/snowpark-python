#
# Copyright (c) 2012-2022 Snowflake Computing Inc. All rights reserved.
#
import os
from typing import Optional

from snowflake.snowpark._internal.analyzer.analyzer_utils import (
    attribute_to_schema_string,
    create_temp_table_statement,
)
from snowflake.snowpark._internal.utils import (
    TempObjectType,
    random_name_for_temp_object,
)
from snowflake.snowpark.dataframe import DataFrame
from snowflake.snowpark.functions import array_agg, col, parse_json, sql_expr
from snowflake.snowpark.session import Session, _get_active_session
from snowflake.snowpark.types import ArrayType


class Transformer:
    def __init__(self):
        self._cols = []

    def fit(self, df: DataFrame) -> "Transformer":
        pass

    def transform(self, df: DataFrame) -> DataFrame:
        pass

    def fit_transform(self, df: DataFrame) -> DataFrame:
        pass

    def save(self, file_path: str, session: Optional[Session] = None) -> None:
        pass

    def load(self, file_path: str, session: Optional[Session] = None) -> "Transformer":
        pass


class StandardScaler(Transformer):
    def __init__(self):
        super().__init__()
        self._fitted_table_name = None
        self._fitted_table_cols = []

    def fit(self, df: DataFrame) -> "StandardScaler":
        self._cols = df.columns
        self._fitted_table_cols = [f"fitted_{c}" for c in self._cols]
        self._fitted_table_name = random_name_for_temp_object(TempObjectType.TABLE)
        df.describe(self._cols, stats=["mean", "stddev_pop"]).select(
            [
                array_agg(c).within_group("summary").as_(fc)
                for c, fc in zip(self._cols, self._fitted_table_cols)
            ]
        ).write.save_as_table(self._fitted_table_name, create_temp_table=True)
        return self

    def transform(self, df: DataFrame) -> DataFrame:
        if self._cols:
            assert self._cols == df.columns
        else:
            self._cols = df.columns
            self._fitted_table_cols = [f"fitted_{c}" for c in self._cols]

        mean_std_table = df._session.table(self._fitted_table_name)
        return (
            mean_std_table.join(df)
            .select(
                [
                    (df[c] - col(fc)[0]) / col(fc)[1]
                    for c, fc in zip(self._cols, self._fitted_table_cols)
                ]
            )
            .to_df(self._cols)
        )

    def save(self, file_path: str, session: Optional[Session] = None) -> None:
        session = session or _get_active_session()
        remote_file_path = (
            f"{session.get_session_stage()}/{os.path.basename(file_path)}"
        )
        session.table(self._fitted_table_name).write.copy_into_location(
            remote_file_path,
            file_format_type="parquet",
            header=True,
            overwrite=True,
            single=True,
        )
        session.file.get(remote_file_path, os.path.dirname(file_path))

    def load(
        self, file_path: str, session: Optional[Session] = None
    ) -> "StandardScaler":
        session = session or _get_active_session()
        remote_file_path = (
            f"{session.get_session_stage()}/{os.path.basename(file_path)}"
        )
        session.file.put(
            file_path, session.get_session_stage(), auto_compress=False, overwrite=True
        )
        self._fitted_table_name = random_name_for_temp_object(TempObjectType.TABLE)
        df = session.read.parquet(remote_file_path)
        cols = []
        for i in range(len(df._reader._user_schema.fields)):
            df._reader._user_schema.fields[i].datatype = ArrayType()
            cols.append(df._reader._user_schema.fields[i].name)
        create_table_query = create_temp_table_statement(
            self._fitted_table_name,
            attribute_to_schema_string(df._reader._user_schema._to_attributes()),
        )
        session.sql(create_table_query).collect()
        df.copy_into_table(
            self._fitted_table_name,
            transformations=[parse_json(sql_expr(f"$1:{c}")).as_(c) for c in cols],
        )
        return self
