"""Regression tests for vmi/bom.py's pure tree / layout / Excel helpers.

Fixture: a small 3-assembly motor with an assembly-level qty (Covers ×2) and
a third level (Rotor -> Magnet Set -> Magnet ×10), exercising qty roll-up
through every code path.
"""

import pytest

from vmi.bom import (
    new_node, node_value, iter_leaves, flatten_parts, group_totals,
    sankey_layout, bom_to_rows, bom_from_rows, compare_groups, BOM_TEMPLATES,
)


def make_tree():
    return new_node("Motor", children=[
        new_node("Stator", children=[
            new_node("Housing", 1, 100, 1000, "Mechanical", False),
            new_node("Winding", 1, 300, 500, "Electrical", True),
        ]),
        new_node("Covers", qty=2, children=[
            new_node("Cover", 1, 50, 200, "Mechanical", False),
            new_node("Screws", 4, 2, 5, "Mechanical", False),
        ]),
        new_node("Rotor", children=[
            new_node("Magnet Set", children=[
                new_node("Magnet", 10, 20, 15, "Electrical", True),
            ]),
        ]),
    ])


# Totals: cost = 400 (Stator) + 2*(50+8) (Covers) + 200 (Rotor) = 716
#         weight = 1500 + 2*(200+20) + 150 = 2090
TOTAL_COST = 716.0
TOTAL_WEIGHT = 2090.0


class TestTreeMath:
    def test_total_cost(self):
        assert node_value(make_tree(), "cost") == pytest.approx(TOTAL_COST)

    def test_total_weight(self):
        assert node_value(make_tree(), "weight") == pytest.approx(TOTAL_WEIGHT)

    def test_assembly_qty_multiplies_subtree(self):
        tree = make_tree()
        covers = tree["children"][1]
        assert node_value(covers, "cost") == pytest.approx(2 * (50 + 4 * 2))

    def test_iter_leaves_effective_qty(self):
        eff = {leaf["name"]: q for _, leaf, q in iter_leaves(make_tree())}
        assert eff == {"Housing": 1, "Winding": 1, "Cover": 2,
                       "Screws": 8, "Magnet": 10}

    def test_flatten_parts_values_sum_to_total(self):
        parts = flatten_parts(make_tree(), "cost")
        assert sum(p["value"] for p in parts) == pytest.approx(TOTAL_COST)
        by_name = {p["name"]: p for p in parts}
        assert by_name["Screws"]["value"] == pytest.approx(16.0)
        assert by_name["Magnet"]["assembly"] == "Rotor"
        assert by_name["Magnet"]["active"] is True

    def test_group_totals_sorted_desc(self):
        g = group_totals(make_tree(), "cost", "Top Assembly")
        assert list(g.keys()) == ["Stator", "Rotor", "Covers"]
        assert g["Covers"] == pytest.approx(116.0)

    def test_group_totals_active(self):
        g = group_totals(make_tree(), "cost", "Active / Non-active")
        assert g["Active"] == pytest.approx(300 + 200)   # Winding + Magnets
        assert g["Non-active"] == pytest.approx(TOTAL_COST - 500)

    def test_group_totals_category(self):
        g = group_totals(make_tree(), "weight", "Category")
        assert g["Electrical"] == pytest.approx(500 + 150)
        assert g["Mechanical"] == pytest.approx(TOTAL_WEIGHT - 650)


class TestSankeyLayout:
    def test_root_and_totals(self):
        lay = sankey_layout(make_tree(), "cost", min_share=0.0)
        assert lay["total"] == pytest.approx(TOTAL_COST)
        root = [n for n in lay["nodes"] if n["depth"] == 0][0]
        assert root["value"] == pytest.approx(TOTAL_COST)
        assert (root["y0"], root["y1"]) == (1.0, 0.0)

    def test_node_values_are_true_values(self):
        # Node "value" must be the real rolled-up value, not the gap-shrunk
        # span x total.
        lay = sankey_layout(make_tree(), "cost", min_share=0.0)
        d1 = {n["label"]: n["value"] for n in lay["nodes"] if n["depth"] == 1}
        assert d1 == {"Stator": pytest.approx(400.0),
                      "Covers": pytest.approx(116.0),
                      "Rotor": pytest.approx(200.0)}

    def test_children_spans_inside_parent_and_proportional(self):
        lay = sankey_layout(make_tree(), "cost", min_share=0.0, gap_frac=0.15)
        d1 = sorted((n for n in lay["nodes"] if n["depth"] == 1),
                    key=lambda n: -n["value"])
        # Sorted max -> min top-down, all inside [0, 1].
        assert d1[0]["label"] == "Stator"
        for n in d1:
            assert 0.0 <= n["y1"] < n["y0"] <= 1.0
        # Span heights proportional to value (same usable factor).
        h = [n["y0"] - n["y1"] for n in d1]
        v = [n["value"] for n in d1]
        assert h[0] / h[1] == pytest.approx(v[0] / v[1])

    def test_min_share_folds_into_others(self):
        lay = sankey_layout(make_tree(), "cost", min_share=0.3)
        d1 = {n["label"]: n["value"] for n in lay["nodes"] if n["depth"] == 1}
        # Covers (16%) and Rotor (28%) fold into one Others band.
        assert set(d1) == {"Stator", "Others"}
        assert d1["Others"] == pytest.approx(316.0)

    def test_depth_limit(self):
        lay = sankey_layout(make_tree(), "cost", max_depth=1, min_share=0.0)
        assert max(n["depth"] for n in lay["nodes"]) == 1

    def test_links_leave_parent_contiguously(self):
        lay = sankey_layout(make_tree(), "cost", min_share=0.0)
        root_links = sorted((l for l in lay["links"] if l["depth"] == 0),
                            key=lambda l: -l["py0"])
        assert root_links[0]["py0"] == pytest.approx(1.0)
        for a, b in zip(root_links, root_links[1:]):
            assert a["py1"] == pytest.approx(b["py0"])
        assert root_links[-1]["py1"] == pytest.approx(0.0)

    def test_empty_tree(self):
        lay = sankey_layout(new_node("Motor"), "cost")
        assert lay["nodes"] == [] and lay["total"] == 0.0


class TestExcelRoundTrip:
    def test_totals_preserved(self):
        rows = bom_to_rows(make_tree())
        rebuilt = bom_from_rows(rows)
        assert node_value(rebuilt, "cost") == pytest.approx(TOTAL_COST)
        assert node_value(rebuilt, "weight") == pytest.approx(TOTAL_WEIGHT)

    def test_qty_folded_on_export(self):
        rows = {r["Part"]: r for r in bom_to_rows(make_tree())}
        assert rows["Screws"]["Qty"] == pytest.approx(8.0)   # 2 (asm) x 4
        assert rows["Magnet"]["Sub-assembly"] == "Magnet Set"
        assert rows["Winding"]["Active"] == "Yes"

    def test_tags_preserved(self):
        rebuilt = bom_from_rows(bom_to_rows(make_tree()))
        parts = {p["name"]: p for p in flatten_parts(rebuilt, "cost")}
        assert parts["Winding"]["category"] == "Electrical"
        assert parts["Winding"]["active"] is True
        assert parts["Housing"]["active"] is False

    def test_forgiving_headers(self):
        rows = [{"assembly": "A", "PART": "X", "Quantity": 2,
                 "Cost per unit": 10, "Weight (grams)": 5,
                 "category": "electrical", "ACTIVE": "yes"}]
        tree = bom_from_rows(rows)
        assert node_value(tree, "cost") == pytest.approx(20.0)
        p = flatten_parts(tree, "cost")[0]
        assert p["category"] == "Electrical" and p["active"] is True

    def test_rows_missing_part_skipped(self):
        rows = [{"Assembly": "A", "Part": None, "Qty": 1},
                {"Assembly": "A", "Part": "Real", "Qty": 1,
                 "Unit Cost (₹)": 5, "Unit Weight (g)": 1}]
        tree = bom_from_rows(rows)
        assert len(list(iter_leaves(tree))) == 1


class TestCompareGroups:
    def make_b(self):
        # Variant: no Rotor, pricier Stator, new Controller assembly.
        return new_node("Motor B", children=[
            new_node("Stator", children=[
                new_node("Housing", 1, 150, 1100, "Mechanical", False),
                new_node("Winding", 1, 350, 550, "Electrical", True),
            ]),
            new_node("Controller", children=[
                new_node("PCB", 1, 400, 100, "Electronics", False),
            ]),
        ])

    def test_union_of_keys_with_zeros(self):
        rows = compare_groups(make_tree(), self.make_b(), "cost",
                              "Top Assembly")
        d = {k: (a, b) for k, a, b in rows}
        assert d["Stator"] == (pytest.approx(400.0), pytest.approx(500.0))
        assert d["Rotor"] == (pytest.approx(200.0), 0.0)     # only in A
        assert d["Controller"] == (0.0, pytest.approx(400.0))  # only in B
        assert d["Covers"] == (pytest.approx(116.0), 0.0)

    def test_sorted_by_max_desc(self):
        rows = compare_groups(make_tree(), self.make_b(), "cost",
                              "Top Assembly")
        maxes = [max(a, b) for _k, a, b in rows]
        assert maxes == sorted(maxes, reverse=True)

    def test_compare_by_active(self):
        rows = compare_groups(make_tree(), self.make_b(), "cost",
                              "Active / Non-active")
        d = {k: (a, b) for k, a, b in rows}
        assert d["Active"] == (pytest.approx(500.0), pytest.approx(350.0))

    def test_identical_trees_zero_delta(self):
        rows = compare_groups(make_tree(), make_tree(), "weight",
                              "Top Assembly")
        assert all(a == pytest.approx(b) for _k, a, b in rows)


class TestTemplates:
    @pytest.mark.parametrize("name", list(BOM_TEMPLATES.keys()))
    def test_templates_have_positive_totals(self, name):
        t = BOM_TEMPLATES[name]
        assert node_value(t, "cost") > 0
        assert node_value(t, "weight") > 0
        # Every leaf carries a category the UI knows.
        from vmi.bom import CATEGORY_CHOICES
        for _, leaf, _q in iter_leaves(t):
            assert leaf["category"] in CATEGORY_CHOICES

    def test_templates_round_trip_excel(self):
        for t in BOM_TEMPLATES.values():
            rebuilt = bom_from_rows(bom_to_rows(t))
            assert node_value(rebuilt, "cost") == pytest.approx(
                node_value(t, "cost"))
            assert node_value(rebuilt, "weight") == pytest.approx(
                node_value(t, "weight"))
