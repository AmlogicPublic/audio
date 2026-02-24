#!/usr/bin/env python3
"""
Audio Spec 生成器
"""

import re
from pathlib import Path
import yaml

from config import CHIP, MACROS, MODULES, CLK_INPUTS

# ============================================================================
# 辅助定义（派生规则，放这里不污染 config）
# ============================================================================

# 模块 gate 映射：模块名 → MACROS 路径
MODULE_GATE = {
    "pdm_a":         ("pdm", "A", "imp"),
    "pdm_b":         ("pdm", "B", "imp"),
    "resample_a":    ("resample", "A", "imp"),
    "resample_b":    ("resample", "B", "imp"),
    "resample_c":    ("resample", "C", "imp"),
    "eq_drc":        ("eqdrc", "imp"),
    "earctx_cmdc":   ("earctx", "imp"),
    "earctx_dmac":   ("earctx", "imp"),
    "earctx_top":    ("earctx", "imp"),
    "earcrx_cmdc":   ("earcrx", "imp"),
    "earcrx_dmac":   ("earcrx", "imp"),
    "earcrx_top":    ("earcrx", "imp"),
    "locker_a":      ("locker", "A", "imp"),
    "locker_b":      ("locker", "B", "imp"),
    "acc_wrapper_asrc":  ("acc_wrapper", "ASRC", "imp"),
    "acc_wrapper_eqdrc": ("acc_wrapper", "EQDRC", "imp"),
    "sed":           ("voice", "algo"),  # voice.algo == "sed" 时激活
    "vad":           ("voice", "algo"),  # voice.algo == "vad" 时激活
}

# ============================================================================
# 工具函数
# ============================================================================


def get_macro(*path):
    """从 MACROS 树中取值"""
    node = MACROS
    for p in path:
        if not isinstance(node, dict) or p not in node:
            return 0
        node = node[p]
    return node if not isinstance(node, dict) else node.get("imp", 0)


def check_condition(cond):
    """检查单个条件"""
    if not cond:
        return True
    path = cond.get("macro_path", [])
    node = MACROS
    for p in path:
        if not isinstance(node, dict) or p not in node:
            return False
        node = node[p]
    if "equals" in cond:
        return node == cond["equals"]
    if "eq" in cond:
        return node == cond["eq"]
    if "ne" in cond:
        return node != cond["ne"]
    return bool(node)


def parse_bits(bits_str):
    """解析 bits 字符串，返回 (high, low)"""
    bits_str = str(bits_str)
    if ":" in bits_str:
        parts = bits_str.split(":")
        return int(parts[0]), int(parts[1])
    return int(bits_str), int(bits_str)


def render_fields(fields):
    """渲染 bit field 表格"""
    if not fields:
        return ""

    lines = ["| Bits | Name | Access | Default | Description |",
             "|------|------|--------|---------|-------------|"]

    covered = set()
    parsed_fields = []

    for f in fields:
        high, low = parse_bits(f["bits"])
        for b in range(low, high + 1):
            covered.add(b)
        cond = f.get("condition")
        active = check_condition(cond)
        parsed_fields.append({
            "high": high,
            "low": low,
            "name": f["name"] if active else "reserved",
            "access": f.get("access", "R/W") if active else "R/W",
            "default": f.get("default", "0") if active else "0",
            "description": f.get("description", "") if active else "reserved",
        })

    parsed_fields.sort(key=lambda x: -x["high"])

    for f in parsed_fields:
        bits_str = f"{f['high']}:{f['low']}" if f["high"] != f["low"] else str(
            f["high"])
        lines.append(
            f"| {bits_str} | {f['name']} | {f['access']} | {f['default']} | {f['description']} |")

    return "\n".join(lines)


def is_active(mod_name):
    """模块是否启用"""
    gate = MODULE_GATE.get(mod_name)
    if gate is None:
        return False
    # voice 模块互斥：sed 和 vad 只能激活一个
    if mod_name == "sed":
        return get_macro("voice", "imp") and get_macro("voice", "algo") == "sed"
    if mod_name == "vad":
        return get_macro("voice", "imp") and get_macro("voice", "algo") == "vad"
    return get_macro(*gate)


def strike(text):
    return f"~~{text}~~"


# ============================================================================
# 文档生成
# ============================================================================

def gen_base_table():
    lines = ["### Module Base Addresses", "",
             "| Module | Address |", "|--------|---------|"]
    for name, addr in sorted(MODULES.items(), key=lambda x: x[1]):
        addr_str = f"0x{addr:08X}"

        # 检查是否在 MODULE_GATE 中且未启用
        if name in MODULE_GATE:
            active = is_active(name)
            if not active:
                lines.append(f"| {strike(name)} | {strike(addr_str)} |")
                continue

        lines.append(f"| {name} | {addr_str} |")
    return "\n".join(lines)


def gen_clk_table():
    lines = ["### Clock Input Sources", ""]
    for group, inputs in CLK_INPUTS.items():
        lines.extend(["| Port | Driver |", "|------|--------|"])
        for suffix, driver in inputs.items():
            lines.append(f"| I_{group}_{suffix} | {driver} |")
        lines.append("")
    return "\n".join(lines)


def gen_macro_table():
    lines = ["### Macro Parameters", "",
             "| Path | Value |", "|------|-------|"]

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        else:
            lines.append(f"| {path} | {node} |")

    walk(MACROS)
    return "\n".join(lines)


def apply_rename_rules(reg_name, rules):
    """应用重命名规则"""
    if not rules:
        return reg_name
    for rule in rules:
        pattern = rule.get("pattern", "")
        replacement = rule.get("replacement", "")
        reg_name = re.sub(pattern, replacement, reg_name)
    return reg_name


def check_module_condition(data):
    """检查模块级条件"""
    mod_cond = data.get("module_condition")
    if mod_cond and not check_condition(mod_cond):
        return False
    
    arch_cond = data.get("arch_condition")
    if arch_cond and not check_condition(arch_cond):
        return False
    
    return True


def render_register(reg, lines, rename_rules=None):
    """渲染单个寄存器"""
    rname = reg["name"]
    
    # 应用重命名规则
    if rename_rules:
        rname = apply_rename_rules(rname, rename_rules)
    
    offsets = reg.get("offsets")
    
    # 处理 voice_param 参数化
    if reg.get("voice_param"):
        voice_algo = get_macro("voice", "algo")
        if isinstance(voice_algo, str):
            algo_upper = voice_algo.upper()
            n = rname.replace("{VOICE}", algo_upper)
            n = n.replace("{TO_VOICE}", f"TO{algo_upper}")
            off = reg.get("offset", 0)
            lines.append(f"#### {n} @ 10'h{off:03X}")
            lines.append("")
            desc = reg.get("description", "")
            if desc:
                lines.append(desc)
                lines.append("")
            fields = reg.get("fields")
            if fields:
                lines.append(render_fields(fields))
                lines.append("")
    elif isinstance(offsets, dict):
        instances = reg.get("instances", list(offsets.keys()))
        conditions = reg.get("conditions", {})
        for idx in instances:
            if idx not in offsets:
                continue
            # 检查实例级条件
            inst_cond = conditions.get(idx)
            if inst_cond and not check_condition(inst_cond):
                continue
            off = offsets[idx]
            # 支持多种占位符格式: {X}, {IDX}, 等
            n = re.sub(r'\{[A-Z_]+\}', idx, rname)
            lines.append(f"#### {n} @ 10'h{off:03X}")
            lines.append("")
            desc = reg.get("description", "")
            if desc:
                lines.append(desc)
                lines.append("")
            fields = reg.get("fields")
            if fields:
                lines.append(render_fields(fields))
                lines.append("")
    else:
        off = reg.get("offset", 0)
        lines.append(f"#### {rname} @ 10'h{off:03X}")
        lines.append("")
        desc = reg.get("description", "")
        if desc:
            lines.append(desc)
            lines.append("")
        fields = reg.get("fields")
        if fields:
            lines.append(render_fields(fields))
            lines.append("")


def gen_module_regs(yaml_path):
    """生成单个模块的寄存器章节"""
    data = yaml.safe_load(yaml_path.read_text())
    module_name = data["module"]
    instances = data.get("instances", {})
    sections = data.get("sections", {})

    all_lines = []

    # 处理 sections 结构（如 EARCTX/EARCRX）
    if sections:
        # 检查模块级条件
        if not check_module_condition(data):
            for section_name in sections.keys():
                inst_key = f"{module_name.lower()}_{section_name.lower()}"
                lines = [f"### {module_name}_{section_name}", ""]
                lines.append(f"*Not present in {CHIP}*")
                all_lines.extend(lines)
                all_lines.append("")
            return "\n".join(all_lines)
        
        # 遍历每个 section（如 CMDC, DMAC, TOP）
        for section_name, section_data in sections.items():
            inst_key = f"{module_name.lower()}_{section_name.lower()}"
            lines = [f"### {module_name}_{section_name}", ""]
            
            if inst_key in MODULE_GATE and not is_active(inst_key):
                lines.append(f"*Not present in {CHIP}*")
            elif inst_key not in MODULES:
                lines.append(f"*Not defined in MODULES*")
            else:
                for reg in section_data.get("registers", []):
                    render_register(reg, lines)
            
            all_lines.extend(lines)
            all_lines.append("")
        
        return "\n".join(all_lines)
    
    # 如果没有实例定义，当作单例模块
    if not instances:
        mod_key = module_name.lower()
        lines = [f"### {module_name}", ""]

        # 检查模块级条件
        if not check_module_condition(data):
            lines.append(f"*Not present in {CHIP}*")
            all_lines.extend(lines)
            return "\n".join(all_lines)

        if mod_key in MODULE_GATE and not is_active(mod_key):
            lines.append(f"*Not present in {CHIP}*")
        else:
            for reg in data.get("registers", []):
                render_register(reg, lines)

        all_lines.extend(lines)
    else:
        # 有多个实例
        for inst_id, inst_cfg in instances.items():
            inst_key = f"{module_name.lower()}_{inst_id.lower()}"
            lines = [f"### {module_name}_{inst_id}", ""]

            if inst_key in MODULE_GATE and not is_active(inst_key):
                lines.append(f"*Not present in {CHIP}*")
            elif inst_key not in MODULES:
                lines.append(f"*Not defined in MODULES*")
            else:
                rename_rules = inst_cfg.get("rename_rules") if inst_cfg else None
                for reg in data.get("registers", []):
                    render_register(reg, lines, rename_rules)

            all_lines.extend(lines)
            all_lines.append("")

    return "\n".join(all_lines)


# ============================================================================
# 主流程
# ============================================================================

def main():
    modules_dir = Path("modules")
    assert modules_dir.exists()

    lines = [
        f"# {CHIP} Audio Register Specification",
        "",
        gen_base_table(),
        "",
        "---",
        "",
        "## Register Details",
        "",
    ]

    for yaml_path in sorted(modules_dir.glob("*.yaml")):
        lines.extend([gen_module_regs(yaml_path), ""])

    lines.extend(["---", "", gen_clk_table(), "---", "", gen_macro_table()])

    Path("generated_regs.md").write_text("\n".join(lines))
    print("输出: generated_regs.md")


if __name__ == "__main__":
    main()
