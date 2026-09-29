"""The tshina EU-label export is parsed row by row, whole rows or nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from scripts.import_tire_labels import COLUMNS, Label, parse_row, read_labels

if TYPE_CHECKING:
    from pathlib import Path

GOOD = {
    "sku": "00000044795",
    "energy_сlass": "c",
    "wet_grip_class": "B",
    "external_rolling_noise_value": "71",
    "external_rolling_noise_class": "B",
    "eprel_registration_number": "1234567",
}


def test_a_good_row() -> None:
    assert parse_row(GOOD) == Label("00000044795", "C", "B", 71, "B", "1234567")


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("sku", ""),
        ("energy_сlass", "H"),
        ("energy_сlass", ""),
        ("wet_grip_class", "Z"),
        ("external_rolling_noise_class", "D"),
        ("external_rolling_noise_value", "7l"),
        ("external_rolling_noise_value", "120"),
        ("external_rolling_noise_value", "40"),
    ],
)
def test_a_value_off_the_eu_scale_rejects_the_row(column: str, value: str) -> None:
    assert parse_row({**GOOD, column: value}) is None


def test_the_file_is_read_by_the_export_header(tmp_path: Path) -> None:
    path = tmp_path / "labels.tsv"
    rows = [
        "\t".join(COLUMNS),
        "\t".join(GOOD[c] for c in COLUMNS),
        "\t".join({**GOOD, "sku": "2", "wet_grip_class": "X"}[c] for c in COLUMNS),
        "\t".join({**GOOD, "external_rolling_noise_value": "70"}[c] for c in COLUMNS),
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    labels, rejected = read_labels(path)
    assert rejected == 1
    (only,) = labels  # the same SKU twice: the last row wins
    assert only.noise_db == 70
