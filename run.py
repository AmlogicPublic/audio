#!/usr/bin/env python3
"""Audio Spec 文档生成器（基于 modules/desc/*.yaml）"""

import sys
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from config import CHIP, MODULE_TREE, CLK_INPUTS


DESC_DIR = Path("modules") / "desc"
COMMON_YAML = DESC_DIR / "_common.yaml"
MODULES_DIR = Path("modules")
MEDIA_DIR = Path("media")
OUTPUT_DIR = Path("output")


PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")

_fig_section = ""
_fig_counter = 0


def set_fig_section(section: str):
    global _fig_section, _fig_counter
    _fig_section = section
    _fig_counter = 0


# ============================================================================
# YAML 寄存器解析（支持 registers0/1/... 和 offset_base0/1/...）
# ============================================================================

@dataclass
class Field:
    bits: str
    name: str
    access: str = "R/W"
    default: str = "0"
    desc: str = ""


@dataclass
class Reg:
    name: str
    offset: int
    fields: list[Field] = field(default_factory=list)
    desc: str = ""


@dataclass
class Module:
    name: str
    addr: int = 0
    offset: int = 0
    active: bool = True
    regs: list[Reg] = field(default_factory=list)
    children: list = field(default_factory=list)


YAML_CACHE: dict[str, tuple[list[Reg], int]] = {}


def _check_yaml_cond(cond) -> bool:
    if not cond:
        return True
    path = cond.get("macro_path", [])
    val = get_cfg(*path)
    if val is None:
        return False
    if "eq" in cond:
        return val == cond["eq"]
    if "equals" in cond:
        return val == cond["equals"]
    return bool(val)


def _resolve_voice(s: str) -> str:
    sed_imp = get_cfg("sed", "imp")
    vad_imp = get_cfg("vad", "imp")
    algo = "SED" if sed_imp else ("VAD" if vad_imp else None)
    if algo:
        s = s.replace("{VOICE}", algo).replace("{TO_VOICE}", f"TO{algo}")
    return s


def _parse_fields(raw: list) -> list[Field]:
    if not raw:
        return []
    out = []
    for f in raw:
        active = _check_yaml_cond(f.get("condition"))
        bits = str(f["bits"])
        out.append(Field(
            bits=bits,
            name=f["name"] if active else "reserved",
            access=f.get("access", "R/W") if active else "R/W",
            default=str(f.get("default", "0")) if active else "0",
            desc=f.get("description", "") if active else "",
        ))
    out.sort(key=lambda x: -int(x.bits.split(":")[0]))
    return out


def _expand_regs(raw: list, inst_id: str = None) -> list[Reg]:
    out = []
    for r in raw:
        name = _resolve_voice(r["name"])
        if inst_id:
            name = re.sub(r'\{[A-Z_]+\}', inst_id, name)
        fields = _parse_fields(r.get("fields", []))
        desc = r.get("description", "")
        offsets = r.get("offsets")

        if isinstance(offsets, dict):
            for idx, off in offsets.items():
                if not _check_yaml_cond(r.get("conditions", {}).get(idx)):
                    continue
                n = re.sub(r'\{[A-Z_]+\}', idx, name)
                out.append(Reg(n, off, fields, desc))
        else:
            out.append(Reg(name, r.get("offset", 0), fields, desc))
    return out


def _collect_register_groups(data: dict) -> list[tuple[str, list]]:
    """收集 registers0, registers1, ... 或单独的 registers"""
    groups = []
    i = 0
    while True:
        key = f"registers{i}"
        if key in data:
            groups.append((key, data[key]))
            i += 1
        else:
            break
    return groups


def _collect_offset_bases(icfg: dict) -> list[tuple[str, int]]:
    """收集 offset_base0, offset_base1, ... 或单独的 offset_base"""
    bases = []
    i = 0
    while True:
        key = f"offset_base{i}"
        if key in icfg:
            bases.append((key, icfg[key]))
            i += 1
        else:
            break
    return bases


def load_all_yaml():
    """加载所有模块 YAML（支持 registers0/1/...、sections、submodules）"""
    if YAML_CACHE:
        return
    for p in MODULES_DIR.glob("*.yaml"):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not data or "module" not in data:
            continue
        mod_name = _resolve_voice(data["module"]).lower()
        if not _check_yaml_cond(data.get("module_condition")):
            continue

        # 处理 sections（如 earcrx, earctx）
        for sec_name, sec_data in data.get("sections", {}).items():
            reg_groups = _collect_register_groups(sec_data)
            all_regs: list[Reg] = []
            for _, raw_regs in reg_groups:
                regs = _expand_regs(raw_regs, None)
                all_regs.extend(regs)
            cache_key = f"{mod_name}_{sec_name}".lower()
            YAML_CACHE[cache_key] = (all_regs, 0)

        # 处理 submodules（如 spdif）
        for sub_name, sub_data in data.get("submodules", {}).items():
            reg_groups = _collect_register_groups(sub_data)
            for iid, icfg in sub_data.get("instances", {}).items():
                icfg = icfg or {}
                if not _check_yaml_cond(icfg.get("condition")):
                    continue
                offset_bases = _collect_offset_bases(icfg)
                all_regs = []
                base_offset = offset_bases[0][1] if offset_bases else 0
                for idx, (_, raw_regs) in enumerate(reg_groups):
                    if idx < len(offset_bases):
                        base_offset = offset_bases[idx][1]
                    regs = _expand_regs(raw_regs, iid)
                    for r in regs:
                        r.offset = base_offset + r.offset
                    all_regs.extend(regs)
                cache_key = f"{sub_name}_{iid}".lower()
                YAML_CACHE[cache_key] = (all_regs, 0)

        # 处理 instances（顶层实例）
        reg_groups = _collect_register_groups(data)
        for iid, icfg in data.get("instances", {}).items():
            icfg = icfg or {}
            if not _check_yaml_cond(icfg.get("condition")):
                continue
            offset_bases = _collect_offset_bases(icfg)
            all_regs = []
            base_offset = 0
            for idx, (_, raw_regs) in enumerate(reg_groups):
                if idx < len(offset_bases):
                    base_offset = offset_bases[idx][1]
                regs = _expand_regs(raw_regs, iid)
                for r in regs:
                    r.offset = base_offset + r.offset
                all_regs.extend(regs)
            cache_key = f"{mod_name}_{iid}".lower()
            YAML_CACHE[cache_key] = (all_regs, 0)

        # 无 instances/sections/submodules 的简单模块
        if not data.get("instances") and not data.get("sections") and not data.get("submodules"):
            offset_bases = _collect_offset_bases(data)
            all_regs = []
            base_offset = offset_bases[0][1] if offset_bases else 0
            for idx, (_, raw_regs) in enumerate(reg_groups):
                if idx < len(offset_bases):
                    base_offset = offset_bases[idx][1]
                regs = _expand_regs(raw_regs, None)
                for r in regs:
                    r.offset = base_offset + r.offset
                all_regs.extend(regs)
            YAML_CACHE[mod_name] = (all_regs, 0)


def get_module_info(name: str) -> tuple[list[Reg], int]:
    load_all_yaml()
    entry = YAML_CACHE.get(name.lower())
    if entry:
        return entry[0], entry[1]
    return [], 0


def _is_leaf(val) -> bool:
    return isinstance(val, dict) and "imp" in val


def _is_module_parent(key: str, val: dict) -> bool:
    if not key[0].islower():
        return False
    subs = [v for k, v in val.items() if not k.startswith("_")]
    return subs and all(_is_leaf(v) for v in subs)


def build_module_tree() -> list[Module]:
    """构建模块树（使用新格式 YAML）"""
    load_all_yaml()

    def build(key: str, val) -> Module:
        if not isinstance(val, dict):
            return None

        if _is_leaf(val):
            addr = val.get("_addr", 0)
            imp = val.get("imp", 0) == 1
            regs, offset = get_module_info(key)
            return Module(key, addr=addr, offset=offset, active=imp, regs=regs)

        if _is_module_parent(key, val):
            children = []
            for k, v in val.items():
                if k.startswith("_"):
                    continue
                full_name = f"{key}_{k}"
                addr = v.get("_addr", 0)
                imp = v.get("imp", 0) == 1
                regs, offset = get_module_info(full_name)
                children.append(Module(full_name, addr=addr, offset=offset, active=imp, regs=regs))
            children.sort(key=lambda m: (not m.active, m.name))
            return Module(key, children=children)

        addr = val.get("_addr", 0)
        children = [build(k, v) for k, v in val.items() if not k.startswith("_")]
        children = [c for c in children if c]
        if children:
            return Module(key, addr=addr, children=children)
        return None

    return [build(k, v) for k, v in MODULE_TREE.items() if build(k, v)]


def tree_md(node: Module, base_addr=0, depth=0, prefix="") -> str:
    """渲染树状结构（含地址信息）"""
    lines = []
    base = node.addr if node.addr else base_addr

    if node.children:
        total = sum(len(c.regs) for c in node.children)
        addr = f" 0x{node.addr:08X}" if node.addr else ""
        lines.append(f"{prefix}{node.name}{addr} ({total} regs)\n")
        for i, c in enumerate(node.children):
            is_child_last = (i == len(node.children) - 1)
            child_prefix = prefix.replace("├─ ", "│  ").replace("└─ ", "   ")
            child_prefix += "└─ " if is_child_last else "├─ "
            lines.append(tree_md(c, base, depth + 1, child_prefix))
    else:
        if node.active and node.regs:
            mod_base = node.addr if node.addr else base + node.offset * 4
            offsets = [r.offset for r in node.regs]
            start, end = min(offsets), max(offsets)
            abs_start, abs_end = mod_base + start * 4, mod_base + end * 4
            size = (end - start + 1) * 4
            lines.append(
                f"{prefix}{node.name}  Base:0x{mod_base:08X} Range:0x{abs_start:08X}-0x{abs_end:08X} {size}B {len(node.regs)}regs\n")
        else:
            lines.append(f"{prefix}[{node.name}] (inactive)\n")

    return "".join(lines)


def clk_md() -> str:
    """渲染时钟源信息"""
    lines = []
    for grp, inputs in CLK_INPUTS.items():
        lines.append(f"### {grp}\n| Port | Driver |\n|---|---|\n")
        for k, v in inputs.items():
            lines.append(f"| I_{grp}_{k} | {v} |\n")
    return "".join(lines)


def get_cfg(*path):
    """从 MODULE_TREE 查找配置值（支持递归查找）"""

    def search(node, remaining):
        if not remaining:
            return node if not isinstance(node, dict) else node.get("imp")
        key = remaining[0]
        if not isinstance(node, dict):
            return None
        if key in node:
            return search(node[key], remaining[1:])
        for v in node.values():
            if isinstance(v, dict):
                result = search(v, remaining)
                if result is not None:
                    return result
        return None

    return search(MODULE_TREE, list(path))


def check_cond(cond) -> bool:
    """desc 的 condition 过滤：支持 macro_path/equal 或 any_of(字符串路径)"""
    if not cond:
        return True
    assert isinstance(cond, dict), f"condition must be dict, got: {type(cond)}"

    if "any_of" in cond:
        any_list = cond["any_of"]
        assert isinstance(any_list, list) and any_list, "condition.any_of must be non-empty list"
        for item in any_list:
            if isinstance(item, str):
                path = [x for x in item.split(".") if x]
                val = get_cfg(*path)
                if val:
                    return True
            elif isinstance(item, dict):
                if check_cond(item):
                    return True
            else:
                assert False, f"unsupported any_of item type: {type(item)}"
        return False

    assert "macro_path" in cond, "condition must have either any_of or macro_path"
    path = cond.get("macro_path", [])
    val = get_cfg(*path)
    assert val is not None, f"macro_path not found: {path}"
    if "eq" in cond:
        return val == cond["eq"]
    if "equals" in cond:
        return val == cond["equals"]
    return bool(val)


def load_yaml(path: Path) -> dict:
    assert path.exists(), f"YAML not found: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"YAML must be dict: {path}"
    return data


def load_common() -> dict:
    assert COMMON_YAML.exists(), f"missing: {COMMON_YAML}"
    return load_yaml(COMMON_YAML)


def count_active_instances(module_key: str) -> int:
    """统计 MODULE_TREE 中某模块的活跃实例数"""
    matches: list[dict] = []
    walk_cfg(MODULE_TREE, module_key, matches)
    if not matches:
        return 0
    count = 0
    for m in matches:
        if "imp" in m:
            if m.get("imp") == 1:
                count += 1
            continue
        for k, v in m.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and v.get("imp") == 1:
                count += 1
    return count


def build_global_ctx() -> dict[str, Any]:
    """构建全局上下文，包含所有模块的计数"""
    ctx: dict[str, Any] = {"CHIP": CHIP}
    module_keys = ["tdmin", "tdmout", "spdifin", "spdifout", "pdm", "toddr", "frddr",
                   "loopback", "resample", "mixer", "eq_drc", "earcrx", "earctx", "tohdmi_dp_tx", "locker"]
    for key in module_keys:
        ctx[f"{key}_count"] = count_active_instances(key)
    return ctx


def load_module_descs() -> dict[str, dict]:
    assert DESC_DIR.exists(), f"missing dir: {DESC_DIR}"
    out: dict[str, dict] = {}
    for p in sorted(DESC_DIR.glob("*.yaml")):
        if p.name.startswith("_"):
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            print(f"[WARN] skip {p.name}: {e.problem}", file=sys.stderr)
            continue
        assert isinstance(data, dict), f"desc yaml must be dict: {p}"
        assert "module" in data, f"desc yaml missing 'module': {p}"
        key = str(data["module"]).strip()
        out[key] = data
    return out


def walk_cfg(node: Any, key: str, out: list[dict]):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and isinstance(v, dict):
                out.append(v)
            walk_cfg(v, key, out)
    elif isinstance(node, list):
        for x in node:
            walk_cfg(x, key, out)


def find_active_module_cfgs(module_key: str) -> list[dict]:
    matches: list[dict] = []
    walk_cfg(MODULE_TREE, module_key, matches)
    assert matches, f"no MODULE_TREE matches for module: {module_key}"

    active_cfgs: list[dict] = []
    for m in matches:
        if "imp" in m:
            if m.get("imp") == 1:
                active_cfgs.append(m)
            continue
        for v in m.values():
            if isinstance(v, dict) and v.get("imp") == 1:
                active_cfgs.append(v)
    return active_cfgs


def collect_placeholders_from_text(s: str) -> set[str]:
    return set(m.group(1) for m in PLACEHOLDER_RE.finditer(s))


def collect_placeholders_from_obj(obj: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, str):
        out |= collect_placeholders_from_text(obj)
    elif isinstance(obj, list):
        for x in obj:
            out |= collect_placeholders_from_obj(x)
    elif isinstance(obj, dict):
        for _, v in obj.items():
            out |= collect_placeholders_from_obj(v)
    return out


def apply_placeholders(s: str, ctx: dict[str, Any]) -> str:
    placeholders = list(collect_placeholders_from_text(s))
    for name in placeholders:
        assert name in ctx, f"missing placeholder: {{{name}}}"
        s = s.replace("{" + name + "}", str(ctx[name]))
    return s


def md_img(src: str, alt: str, with_figure=False) -> str:
    global _fig_counter
    if src.startswith("../") or src.startswith("/") or src.startswith("http"):
        path = src
    elif "/" in src:
        path = src.replace("media/", "../media/", 1)
    else:
        path = f"../media/{src}"
    img = f"![{alt}]({path})"
    if with_figure:
        _fig_counter += 1
        fig_num = f"{_fig_section}.{_fig_counter}" if _fig_section else str(_fig_counter)
        return f"{img}\n\nFigure {fig_num}: {alt}"
    return img


def render_application_notes(enabled_descs: dict[str, dict]) -> str:
    """汇总所有模块的 application_notes"""
    parts = []
    for key, desc in enabled_descs.items():
        notes = desc.get("application_notes")
        if not notes:
            continue
        assert isinstance(notes, list), f"application_notes must be list: {key}"
        display = desc.get("display_name", key)
        for note in notes:
            assert isinstance(note, dict), f"application_notes item must be dict: {key}"
            title = note.get("title", "Untitled")
            parts.append(f"### {display}: {title}\n")
            steps = note.get("steps")
            if steps:
                if isinstance(steps, str):
                    parts.append(steps.rstrip() + "\n")
                else:
                    assert isinstance(steps, list), f"application_notes.steps must be str or list: {key}"
                    for step in steps:
                        parts.append(f"- {step}\n")
            desc_text = note.get("description")
            if desc_text:
                parts.append(f"\n{desc_text}\n")
            parts.append("\n")
    return "".join(parts)


def render_module_block(desc: dict) -> str:
    module_key = str(desc["module"]).strip()
    display = desc.get("display_name", module_key)
    active_cfgs = find_active_module_cfgs(module_key)
    assert active_cfgs, f"module is enabled by condition but has no active cfgs: {module_key}"

    # base ctx
    ctx: dict[str, Any] = {"CHIP": CHIP}
    module_count = len(active_cfgs)
    ctx[module_key + "_count"] = module_count
    ctx["count"] = module_count

    # required placeholders
    placeholders = set()
    for field in ("overview", "features", "block_diagram", "function_description"):
        if field in desc:
            placeholders |= collect_placeholders_from_obj(desc[field])
    # never substitute these implicitly missing keys
    placeholders.discard("CHIP")

    # resolve any non-special placeholders from cfg
    for name in placeholders:
        if name == module_key + "_count" or name == "count":
            continue
        values = []
        for cfg in active_cfgs:
            if name in cfg:
                values.append(cfg[name])
        assert values, f"placeholder {{{name}}} not found in active cfg for module {module_key}"
        # max is a safe default for lane/ch/fifo-depth style params
        ctx[name] = max(int(v) for v in values)

    def render_features() -> str:
        feats = desc.get("features")
        if feats is None:
            return ""
        if isinstance(feats, str):
            return apply_placeholders(feats.rstrip(), ctx) + "\n"

        def render_list(items, indent=0):
            lines = []
            prefix = "  " * indent
            for x in items:
                if isinstance(x, str):
                    lines.append(f"{prefix}- {apply_placeholders(x, ctx)}")
                elif isinstance(x, dict):
                    for k, v in x.items():
                        lines.append(f"{prefix}- {apply_placeholders(k, ctx)}")
                        assert isinstance(v, list), f"nested features value must be list, got: {type(v)}"
                        lines.append(render_list(v, indent + 1))
                else:
                    assert False, f"features item must be str or dict, got: {type(x)}"
            return "\n".join(lines)

        assert isinstance(feats, list), f"features must be str or list, got: {type(feats)}"
        return render_list(feats) + "\n"

    overview = apply_placeholders(desc.get("overview", "").rstrip(), ctx) if desc.get("overview") else ""
    func_desc = apply_placeholders(desc.get("function_description", "").rstrip(), ctx) if desc.get(
        "function_description"
    ) else ""

    block = desc.get("block_diagram") or {}
    block_images = block.get("images") or []
    assert isinstance(block_images, list), f"block_diagram.images must be list: {module_key}"
    img_lines = []
    for img in block_images:
        assert isinstance(img, str), f"block_diagram.images item must be str: {module_key}"
        img_lines.append(md_img(img, alt=display, with_figure=True))

    parts = [f"#### {display}\n"]
    if overview:
        parts.append(overview + "\n")
    feats_md = render_features().rstrip()
    if feats_md:
        parts.append("##### Features\n" + feats_md + "\n")
    if img_lines:
        parts.append("##### Block Diagram\n" + "\n\n".join(img_lines) + "\n")
    if func_desc:
        parts.append("##### Function Description\n" + func_desc + "\n")
    return "".join(parts)


def render_regs_md_with_images(node, depth=0, base_addr=0) -> str:
    level = min(depth + 3, 6)
    base = node.addr if node.addr else base_addr

    if node.children:
        head = f"{'#' * level} {node.name}\n\n"
        return head + "".join(
            render_regs_md_with_images(c, depth + 1, base_addr=base) for c in node.children
        )

    if not node.active:
        return f"{'#' * level} ~~{node.name}~~ *Not in {CHIP}*\n\n"

    if not node.regs:
        return f"{'#' * level} {node.name} (0 regs)\n\n"

    lines = [f"{'#' * level} {node.name} ({len(node.regs)} regs)\n\n"]
    mod_base = node.addr if node.addr else base + node.offset * 4

    for r in node.regs:
        abs_addr = mod_base + r.offset * 4
        addr_expr = f"0x{mod_base:08X} + 0x{r.offset:03X} * 4 = 0x{abs_addr:08X}"
        lines.append(f'<a id="reg-{r.name}"></a>\n')
        lines.append(f'<div class="reg-hdr"><span class="reg-name"><a href="#sum-{r.name}">{r.name}</a></span>')
        lines.append(f'<span class="reg-addr">{addr_expr}</span></div>\n\n')

        img_path = MEDIA_DIR / f"REG_{r.name}.png"
        if img_path.exists():
            lines.append(md_img(f"REG_{r.name}.png", alt=r.name) + "\n\n")

        if r.fields:
            lines.append("| Bits | Name | Access | Default | Description |\n")
            lines.append("|:----:|:-----|:------:|:-------:|:------------|\n")
            for f in r.fields:
                lines.append(f"| {f.bits} | {f.name} | {f.access} | {f.default} | {f.desc} |\n")
            lines.append("\n")
        elif r.desc:
            lines.append(f"{r.desc}\n\n")

    return "".join(lines)


def render_reg_summary_md(node, base_addr=0) -> list[list[str]]:
    """递归收集寄存器 Summary 行: [Name, Offset, Size, Reset Value, Description]"""
    rows: list[list[str]] = []
    base = node.addr if node.addr else base_addr

    if node.children:
        for c in node.children:
            rows.extend(render_reg_summary_md(c, base))
        return rows

    if not node.active or not node.regs:
        return rows

    mod_base = node.addr if node.addr else base + node.offset * 4
    for r in node.regs:
        reg_byte_off = r.offset * 4
        abs_addr = mod_base + reg_byte_off
        reset_val = "0x00000000"
        if r.fields:
            val = 0
            for f in r.fields:
                if f.default and f.default != "0":
                    bits = f.bits
                    if ":" in bits:
                        hi, lo = map(int, bits.split(":"))
                    else:
                        hi = lo = int(bits)
                    try:
                        fv = int(f.default, 0)
                    except ValueError:
                        fv = 0
                    val |= (fv << lo)
            reset_val = f"0x{val:08X}"
        desc = r.desc if r.desc else ""
        rows.append([f'<a id="sum-{r.name}"></a>[{r.name}](#reg-{r.name})', f"0x{abs_addr:08X}", "W", reset_val, desc])
    return rows


def render_register_section(tree) -> str:
    addr_map = "```\n" + "".join(tree_md(n) for n in tree) + "```\n"

    summary_rows = []
    for n in tree:
        summary_rows.extend(render_reg_summary_md(n, base_addr=0))

    summary_md = "| Name | Offset | Size | Reset Value | Description |\n|---|---|---|---|---|\n"
    for row in summary_rows:
        summary_md += f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} |\n"

    reg_details = "".join(render_regs_md_with_images(n, depth=0, base_addr=0) for n in tree)

    parts = []
    parts.append("## 5. Register Description\n")
    parts.append("### 5.1 Internal Address Mapping\n")
    parts.append(addr_map + "\n")
    parts.append("### 5.2 Register Summary\n")
    parts.append(summary_md + "\n")
    parts.append("### 5.3 Detailed Registers Description\n")
    parts.append(reg_details + "\n")
    parts.append("## Clock Sources\n")
    parts.append(clk_md() + "\n")
    return "".join(parts)


LAYOUT_CSS = """
@page {
    size: A4;
    margin: 18mm 14mm 18mm 14mm;
}

:root {
    --img-w: 127mm;
}

body {
    font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    line-height: 1.5;
    color: #1f2937;
    max-width: 1100px;
    margin: 0 auto;
    padding: 20px 16px 40px;
}

h1, h2, h3, h4, h5, h6 {
    color: #0f172a;
    margin-top: 1em;
    margin-bottom: 0.4em;
}

h1 {
    border-bottom: 2px solid #cbd5e1;
    padding-bottom: 0.25em;
}

h2 {
    border-left: 4px solid #3b82f6;
    padding-left: 0.5em;
}

p, li {
    word-break: break-word;
}

img {
    display: block;
    width: var(--img-w);
    max-width: 100%;
    height: auto;
    object-fit: contain;
    margin: 10px auto 8px;
    border: 1px solid #d1d5db;
    border-radius: 4px;
    background: #fff;
    padding: 3px;
    box-sizing: border-box;
}

table {
    border-collapse: collapse;
    width: 100%;
    table-layout: fixed;
    margin: 8px 0 14px;
    font-size: 0.82rem;
}

th, td {
    border: 1px solid #4b5563;
    padding: 4px 6px;
    vertical-align: top;
}

th {
    background: #e5e7eb;
    color: #111827;
    font-weight: 600;
    text-align: center;
    white-space: nowrap;
}

th:nth-child(1) { width: 60px; }
th:nth-child(2) { width: 180px; }
th:nth-child(3) { width: 60px; }
th:nth-child(4) { width: 80px; }
th:nth-child(5) { width: auto; }

td:first-child {
    text-align: center;
    white-space: nowrap;
}

td:nth-child(2) {
    font-weight: 500;
}

td:nth-child(3),
td:nth-child(4) {
    text-align: center;
    white-space: nowrap;
}

.reg-hdr {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    background: #f3f4f6;
    border: 1px solid #9ca3af;
    padding: 5px 10px;
    margin: 16px 0 6px;
    font-size: 0.92rem;
}

.reg-hdr .reg-name {
    font-weight: 700;
    color: #1e40af;
}

.reg-hdr .reg-addr {
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 0.85rem;
    color: #374151;
}

code, pre {
    font-family: "Consolas", "Cascadia Code", "JetBrains Mono", monospace;
}

code {
    font-size: 0.86em;
    background: #f1f5f9;
    padding: 1px 4px;
    border-radius: 3px;
}

pre {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 4px;
    padding: 8px;
    overflow-x: auto;
    white-space: pre;
}

a {
    color: #1d4ed8;
    text-decoration: none;
}

blockquote {
    margin: 10px 0;
    padding: 6px 12px;
    border-left: 3px solid #93c5fd;
    background: #eff6ff;
}

@media print {
    h1, h2, h3, h4 {
        page-break-after: avoid;
    }
    table, figure, pre, blockquote, .reg-hdr {
        page-break-inside: avoid;
    }
    img {
        margin: 5px auto 6px;
    }
}
""".strip()


def write_style_header(header_path: Path):
    header_path.write_text(f"<style>\n{LAYOUT_CSS}\n</style>\n", encoding="utf-8")


def _find_edge_exe() -> Path:
    edge_candidates: list[Path] = []
    edge_in_path = shutil.which("msedge")
    if edge_in_path:
        edge_candidates.append(Path(edge_in_path))
    edge_in_path_alt = shutil.which("microsoft-edge")
    if edge_in_path_alt:
        edge_candidates.append(Path(edge_in_path_alt))

    program_files = Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
    program_files_x86 = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    edge_candidates.extend([program_files, program_files_x86])

    for p in edge_candidates:
        if p.exists():
            return p
    assert False, "未找到 msedge，请安装 Microsoft Edge 或将 msedge 加入 PATH"


def export_html(md_path: Path, title: str) -> Path:
    pandoc = shutil.which("pandoc")
    assert pandoc, "未找到 pandoc，请先安装并加入 PATH"

    html_path = md_path.with_suffix(".html")
    style_header = OUTPUT_DIR / "_style_header.html"
    write_style_header(style_header)

    html_cmd = [
        pandoc,
        str(md_path),
        "-f",
        "gfm",
        "-t",
        "html5",
        "--standalone",
        "--toc",
        "--metadata",
        f"title={title}",
        "-H",
        str(style_header),
        "-o",
        str(html_path),
    ]
    html_ret = subprocess.run(html_cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert html_ret.returncode == 0, "pandoc 转换 HTML 失败"
    print(f"输出: {html_path}")
    return html_path


def export_pdf_from_html(html_path: Path) -> Path:
    pdf_path = html_path.with_suffix(".pdf")
    edge_exe = _find_edge_exe()
    pdf_cmd = [
        str(edge_exe),
        "--headless=new",
        "--disable-gpu",
        "--disable-logging",
        "--log-level=3",
        "--no-first-run",
        "--no-default-browser-check",
        f"--print-to-pdf={pdf_path.resolve()}",
        html_path.resolve().as_uri(),
    ]
    pdf_ret = subprocess.run(pdf_cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert pdf_ret.returncode == 0, "Edge 导出 PDF 失败"
    print(f"输出: {pdf_path}")
    return pdf_path


def export_html_and_pdf(md_path: Path, title: str):
    html_path = export_html(md_path, title)
    export_pdf_from_html(html_path)


def main():
    assert DESC_DIR.exists(), f"missing desc dir: {DESC_DIR}"
    assert COMMON_YAML.exists(), f"missing common yaml: {COMMON_YAML}"
    assert Path("modules").exists(), "missing modules dir"
    assert Path("media").exists(), "missing media dir"

    common = load_common()
    descs = load_module_descs()

    # filter by condition
    enabled_descs = {}
    for k, d in descs.items():
        if check_cond(d.get("condition")):
            enabled_descs[k] = d

    # build cfg-gated register tree
    tree = build_module_tree()

    # build global context with module counts
    global_ctx = build_global_ctx()

    # output md
    OUTPUT_DIR.mkdir(exist_ok=True)
    md_path = OUTPUT_DIR / f"audio_spec_{CHIP}.md"
    if md_path.exists():
        md_path.unlink()

    parts = []
    title = apply_placeholders(common.get("title_template", "{CHIP} Audio Specification"), global_ctx)
    parts.append(f"# {title}\n\n")

    # Overview
    parts.append("## 1. Overview\n")
    overview = common.get("overview", "").rstrip()
    if overview:
        parts.append(apply_placeholders(overview, global_ctx) + "\n\n")

    # Features
    parts.append("## 2. Features\n")
    feats = common.get("features") or []
    if feats:
        assert isinstance(feats, list), "common.features must be list"
        for f in feats:
            parts.append(f"- {apply_placeholders(str(f), global_ctx)}\n")
        parts.append("\n")

    # Block diagram
    set_fig_section("3")
    parts.append("## 3. Block Diagram\n")
    b = common.get("block_diagram") or {}
    b_imgs = b.get("images") or []
    assert isinstance(b_imgs, list), "common.block_diagram.images must be list"
    for img in b_imgs:
        parts.append(md_img(str(img), alt=f"{CHIP} Audio Path", with_figure=True) + "\n\n")

    # Function description (audio path)
    parts.append("## 4. Function Description\n")

    groups = [
        ("Audio Input", ["tdmin", "spdifin", "pdm", "earcrx"]),
        ("Audio Output", ["tdmout", "spdifout", "earctx"]),
        ("DDR Datapath", ["toddr", "frddr"]),
        ("Input Processing", ["resample", "loopback"]),
        ("Output Processing", ["mixer", "eq_drc"]),
        ("Clock and Timing", ["locker"]),
    ]

    for idx, (group_name, module_order) in enumerate(groups):
        set_fig_section(f"4.{idx + 1}")
        parts.append(f"### 4.{idx + 1}. {group_name}\n")
        for m in module_order:
            if m in enabled_descs:
                parts.append(render_module_block(enabled_descs[m]) + "\n")

    parts.append(render_register_section(tree))

    parts.append("## 6. Application Notes\n")
    app_notes_md = render_application_notes(enabled_descs)
    if app_notes_md:
        parts.append(app_notes_md)
    else:
        parts.append("_No application notes defined._\n")

    md_path.write_text("".join(parts), encoding="utf-8")
    print(f"输出: {md_path}")
    export_html_and_pdf(md_path, title)


if __name__ == "__main__":
    main()
