"""
Audio Spec 配置 - 人类编辑区
"""

CHIP = "A9"

# ============================================================================
# 1. 模块树结构 - 手动定义层级
# ============================================================================
MODULE_TREE = {
    "audio_ao_top": {
        "_addr": 0xFFAE1000,
        "Input": {
            "pdm": {"A": 0xFFAE2000, "B": 0xFFAE2400},
        },
        "Voice": {
            "sed": 0xFFAC0000,
            "vad": 0xFE331800,
            "to_voice": "_parent",  # in_ao=1 时在这里
        },
    },
    "audio_ee_top": {
        "_addr": 0xFFAE0000,
        "Input": {
            "tdmin": "_parent",
            "spdifin": "_parent",
            "spdifin_lb": "_parent",
            "frhdmirx": "_parent",
            "fratv": "_parent",
            "earcrx": {
                "CMDC": 0xFFAE5800,
                "DMAC": 0xFFAE5C00,
                "TOP":  0xFFAE5E00,
            },
        },
        "Output": {
            "tdmout": "_parent",
            "spdifout": "_parent",
            "earctx": {
                "CMDC": 0xFFAE5000,
                "DMAC": 0xFFAE5400,
                "TOP":  0xFFAE5600,
            },
            "tohdmitx": "_parent",
        },
        "InputProcessing": {
            "resample": {"A": 0xFFAE3000, "B": 0xFFAE3400, "C": 0xFFAE3800},
            "resample_id": "_parent",
            "loopback": "_parent",
        },
        "OutputProcessing": {
            "mixer": "_parent",
            "eq_drc": 0xFFAE4000,
        },
        "IOProcessing": {
            "toacodec": "_parent",
        },
        "Mem2MemProcessing": {
            "acc_wrapper": {"ASRC": 0xFFAE8400, "EQDRC": 0xFFAE8800},
        },
        "DMA": {
            "toddr": "_parent",
            "frddr": "_parent",
            "ddr_arb": "_parent",
        },
        "Misc": {
            "locker": {"A": 0xFFAE6000, "B": 0xFFAE6400},
            "pcpd_mon": "_parent",
        },
    },
}

# ============================================================================
# 2. 宏定义（对应 macro.hpp，0=off, 1=on, 数值=参数）
# ============================================================================
MACROS = {
    #  soundbar(A series and some T series need 32 ch support)

    # ── 输入设备 ──
    "tdmin": {
        "A": {"imp": 1, "pad_num": 2},
        "B": {"imp": 1, "pad_num": 2},
        "C": {"imp": 1, "pad_num": 8},
        "D": {"imp": 1, "pad_num": 8},
        "LB_A": {"imp": 1, "pad_num": 2},
        "LB_B": {"imp": 1, "pad_num": 8},
    },
    "spdifin": {
        "A": {"imp": 1},
        "B": {"imp": 1},
    },
    "spdifin_lb": {
        "A": {"imp": 1},
        "B": {"imp": 1},
    },
    "pdm": {
        "A": {"imp": 1, "chan_num": 8},
        "B": {"imp": 1, "chan_num": 8},
    },
    "hdmirx": {  # TDM/SPDIF/DSD input interface
        "A": {"imp": 1, "dsdin_imp": 0},
        "B": {"imp": 1, "dsdin_imp": 0},
    },
    "frhdmirx": {  # HDMI RX 前端
        "A": {"imp": 1},
        "B": {"imp": 1},
    },
    "earcrx": {"imp": 1},
    "atv": {"imp": 1},
    "fratv": {"imp": 1},

    # ── 输出设备 ──
    "tdmout": {
        "A": {"imp": 1, "pad_num": 2},
        "B": {"imp": 1, "pad_num": 2},
        "C": {"imp": 1, "pad_num": 8},
        "D": {"imp": 1, "pad_num": 8},
    },
    "spdifout": {
        "A": {"imp": 1, "ch16": 1},  # else 8ch
        "B": {"imp": 1, "ch16": 1},  # else 8ch
    },
    "hdmi_dp_tx": {
        "A": {"imp": 1},
        "B": {"imp": 1}
    },
    "tohdmitx": {"imp": 1},
    "toacodec": {"imp": 1},
    "earctx": {"imp": 1},

    # ── 输入处理 ──
    "loopback": {
        "A": {"imp": 1, "ch": 4},   # 4/8/16, 0=off
        "B": {"imp": 0, "ch": 0},
    },
    "resample": {
        "A": {"imp": 1, "dw": 24, "chnum": 8},
        "B": {"imp": 1, "dw": 24, "chnum": 32},
        "C": {"imp": 1, "dw": 24, "chnum": 32},
    },
    "resample_id": {
        "A": {"imp": 1},
        "B": {"imp": 1},
        "C": {"imp": 1},
    },
    "to_voice": {"A": {"imp": 1}},  # TOSED/TOVAD

    # ── 输出处理 ──
    "eqdrc": {
        "imp": 1,
        "ch_in": 0,
        "static": 1,  # 0=dynamic coeff, 1=static coeff
        "arch": "reg",  # "ram" or "reg"
    },
    "mixer": {"imp": 1},

    # ── DMA ──
    # size = depth*64b         B,C,D need to have the same depth
    "toddr": {
        "A": {"imp": 1, "fifo_depth": 128},
        "B": {"imp": 1, "fifo_depth": 128},
        "C": {"imp": 1, "fifo_depth": 128},
        "D": {"imp": 1, "fifo_depth": 128},
        "E": {"imp": 1, "fifo_depth": 128},
        "ch_sync_depth": 16,  # TV: 32; STB: 16 (no channel sync for FRDDR)
    },
    "frddr": {
        "A": {"imp": 1, "fifo_depth": 128},
        "B": {"imp": 1, "fifo_depth": 128},
        "C": {"imp": 1, "fifo_depth": 128},
        "D": {"imp": 1, "fifo_depth": 128},
        "E": {"imp": 1, "fifo_depth": 128},
    },
    "axi_irq_sync": {"imp": 1},

    # ── MISC ──
    "voice": {
        "imp": 1,
        "algo": "sed",  # vad(传统谱熵检测)/sed(神经网络特征检测)
        "in_ao": 1  # 放在ao还是ee
    },
    "aocdec": {"imp": 1},
    "locker": {"A": {"imp": 1}, "B": {"imp": 0}},
    "pcpd_mon": {"A": {"imp": 1}, "B": {"imp": 0}},
    "pwr_domain": {"imp": 1},
    "ddr_arb": {"imp": 0},
    "acc_wrapper": {
        "ASRC": {"imp": 1},   # ACC for ASRC
        "EQDRC": {"imp": 1},  # ACC for EQ/DRC
    },
}

# ============================================================================
# 3. 从 MODULE_TREE 提取地址表
# ============================================================================


def _extract_addrs(node, prefix=""):
    """递归提取所有地址"""
    result = {}
    for key, val in node.items():
        if key.startswith("_"):
            if key == "_addr":
                result[prefix.rstrip("_").lower()] = val
            continue
        full = f"{prefix}{key}" if prefix else key
        if isinstance(val, int):
            result[full.lower()] = val
        elif val == "_parent":
            pass  # 在父模块内部，无独立地址
        elif isinstance(val, dict):
            result.update(_extract_addrs(val, f"{full}_"))
    return result


MODULES = _extract_addrs(MODULE_TREE)


# ============================================================================
# 模块使用决策
# ============================================================================
def get_active_voice_module():
    """根据宏配置返回当前使用的voice模块"""
    voice_cfg = MACROS.get("voice", {})
    if not voice_cfg.get("imp"):
        return None

    algo = voice_cfg.get("algo", "sed")
    return algo  # 返回 "sed" 或 "vad"


# ============================================================================
# 3. 时钟输入源
# ============================================================================
CLK_INPUTS = {
    "other_pll": {
        "a": "Oscin_clk(24M)",
        "b": "Hifi0_pll_clk",
        "c": "1'b0",
        "d": "Cts_rtc_clk",
        "e": "1'b0",
        "f": "Fclk_div3",
        "g": "Fclk_div4",
        "h": "Fclk_div5",
    },
    "slv": {
        "a": "Mod_audio_slv_sclk0",
        "b": "Mod_audio_slv_sclk1",
        "c": "1'b0",
        "d": "1'b0",
        "e": "1'b0",
        "f": "1'b0",
        "g": "Wifi_beacon_i",
        "h": "1'b0",
        "i": "1'b0",
        "j": "Acodec_ADC",
    },
}
