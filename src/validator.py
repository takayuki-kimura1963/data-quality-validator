import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


def is_blank(value: object) -> bool:
    """値がNULL、空文字、空白文字だけの場合にTrueを返す。"""
    if pd.isna(value):
        return True

    return str(value).strip() == ""


def load_rules(config_path: Path) -> dict:
    """JSON形式の検査ルールを読み込む。"""
    with config_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def add_error(
    errors: list[dict],
    row_number: int,
    column_name: str,
    error_code: str,
    error_message: str,
    value: object,
) -> None:
    """検査エラーを一覧へ追加する。"""
    errors.append(
        {
            "row_number": row_number,
            "column_name": column_name,
            "error_code": error_code,
            "error_message": error_message,
            "value": "" if pd.isna(value) else str(value),
        }
    )


def validate_required(
    df: pd.DataFrame,
    column_name: str,
    errors: list[dict],
) -> None:
    """必須項目の空欄を検査する。"""
    for index, value in df[column_name].items():
        if is_blank(value):
            add_error(
                errors=errors,
                row_number=index + 2,
                column_name=column_name,
                error_code="REQUIRED",
                error_message="必須項目が空欄です",
                value=value,
            )


def validate_primary_key(
    df: pd.DataFrame,
    primary_keys: list[str],
    errors: list[dict],
) -> None:
    """主キーの空欄と重複を検査する。"""
    valid_key_mask = pd.Series(True, index=df.index)

    for column_name in primary_keys:
        for index, value in df[column_name].items():
            if is_blank(value):
                valid_key_mask.loc[index] = False
                add_error(
                    errors=errors,
                    row_number=index + 2,
                    column_name=column_name,
                    error_code="PRIMARY_KEY_EMPTY",
                    error_message="主キーが空欄です",
                    value=value,
                )

    valid_df = df.loc[valid_key_mask]

    duplicate_mask = valid_df.duplicated(
        subset=primary_keys,
        keep="first",
    )

    for index in valid_df.index[duplicate_mask]:
        key_value = " | ".join(
            str(df.loc[index, column_name])
            for column_name in primary_keys
        )

        add_error(
            errors=errors,
            row_number=index + 2,
            column_name=",".join(primary_keys),
            error_code="PRIMARY_KEY_DUPLICATE",
            error_message="主キーが重複しています",
            value=key_value,
        )


def validate_date(
    df: pd.DataFrame,
    column_name: str,
    date_format: str,
    errors: list[dict],
) -> None:
    """日付の形式と実在性を検査する。"""
    for index, value in df[column_name].items():
        if is_blank(value):
            continue

        text = str(value).strip()

        try:
            if date_format == "%Y%m%d" and not re.fullmatch(r"\d{8}", text):
                raise ValueError("8桁の数字ではありません")

            datetime.strptime(text, date_format)

        except ValueError:
            add_error(
                errors=errors,
                row_number=index + 2,
                column_name=column_name,
                error_code="INVALID_DATE",
                error_message=f"日付が不正です。形式: {date_format}",
                value=value,
            )


def validate_integer(
    df: pd.DataFrame,
    column_name: str,
    errors: list[dict],
) -> None:
    """整数として有効な値か検査する。"""
    for index, value in df[column_name].items():
        if is_blank(value):
            continue

        text = str(value).strip()

        if not re.fullmatch(r"[+-]?\d+", text):
            add_error(
                errors=errors,
                row_number=index + 2,
                column_name=column_name,
                error_code="INVALID_INTEGER",
                error_message="整数として認識できません",
                value=value,
            )


def validate_max_length(
    df: pd.DataFrame,
    column_name: str,
    max_length: int,
    errors: list[dict],
) -> None:
    """文字列の最大長を検査する。"""
    for index, value in df[column_name].items():
        if is_blank(value):
            continue

        if len(str(value)) > max_length:
            add_error(
                errors=errors,
                row_number=index + 2,
                column_name=column_name,
                error_code="MAX_LENGTH",
                error_message=f"最大文字数{max_length}を超えています",
                value=value,
            )


def validate_dataframe(df: pd.DataFrame, rules: dict) -> list[dict]:
    """設定されたルールに従ってDataFrameを検査する。"""
    errors: list[dict] = []

    primary_keys = rules.get("primary_key", [])
    column_rules = rules.get("columns", {})

    required_columns = set(primary_keys) | set(column_rules.keys())
    missing_columns = sorted(required_columns - set(df.columns))

    if missing_columns:
        raise ValueError(
            "CSVに必要な列がありません: " + ", ".join(missing_columns)
        )

    validate_primary_key(
        df=df,
        primary_keys=primary_keys,
        errors=errors,
    )

    for column_name, rule in column_rules.items():
        column_type = rule.get("type", "string")

        # 主キーの空欄はPRIMARY_KEY_EMPTYとして検出済みなので、
        # REQUIREDとの二重登録を避ける。
        if rule.get("required", False) and column_name not in primary_keys:
            validate_required(
                df=df,
                column_name=column_name,
                errors=errors,
            )

        if column_type == "date":
            validate_date(
                df=df,
                column_name=column_name,
                date_format=rule.get("format", "%Y%m%d"),
                errors=errors,
            )

        if column_type == "integer":
            validate_integer(
                df=df,
                column_name=column_name,
                errors=errors,
            )

        if "max_length" in rule:
            validate_max_length(
                df=df,
                column_name=column_name,
                max_length=rule["max_length"],
                errors=errors,
            )

    return errors


def run_validation(
    input_path: Path,
    config_path: Path,
    output_path: Path,
) -> int:
    """CSVを読み込み、検査結果を出力する。"""
    rules = load_rules(config_path)

    file_rule = rules.get("file", {})
    encoding = file_rule.get("encoding", "utf-8-sig")
    delimiter = file_rule.get("delimiter", ",")

    df = pd.read_csv(
        input_path,
        encoding=encoding,
        delimiter=delimiter,
        dtype=str,
        keep_default_na=False,
    )

    errors = validate_dataframe(df, rules)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    error_columns = [
        "row_number",
        "column_name",
        "error_code",
        "error_message",
        "value",
    ]

    result_df = pd.DataFrame(errors, columns=error_columns)
    result_df.to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"読込件数  : {len(df)}件")
    print(f"エラー件数: {len(errors)}件")
    print(f"検査結果  : {output_path}")

    return 1 if errors else 0


def parse_arguments() -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(
        description="CSVデータの品質を検査します"
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=Path("sample/input.csv"),
        help="検査対象CSV",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/validation_rules.json"),
        help="検査ルールJSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/validation_errors.csv"),
        help="検査結果CSV",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    try:
        return run_validation(
            input_path=args.input,
            config_path=args.config,
            output_path=args.output,
        )

    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"処理エラー: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
