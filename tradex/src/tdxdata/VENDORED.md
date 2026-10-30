# Vendored: tdxdata

本目录是上游 [tdxdata](https://github.com/liu1561089752/tdxdata) 的**固定版本副本**，
随 tradex wheel 一起打包，避免生产环境依赖 git / 网络安装。

## 固定信息

| 项 | 值 |
|---|---|
| 上游仓库 | https://github.com/liu1561089752/tdxdata |
| 上游版本 | `0.3.0`（`tdxdata/_version.py`） |
| 上游 commit | `a276f57586a8d006f658d2e1d37faa10e681f695` |
| commit 日期 | 2026-10-28 22:59:05 +0800 |
| 同步日期 | 2026-10-30 |
| 许可 | MIT（见同目录 `LICENSE`，Copyright (c) 2026 liu1561089752） |
| 依赖 | 无第三方依赖（纯标准库） |
| Python | >= 3.10 |

## 同步范围

只同步上游 `tdxdata/` 包目录（含 `data/products.json` 与
`_engine/catalog/*.json`），**不同步**上游的 `tests/`、`docs/`、`tools/`、`examples/`。
上游 README / CHANGELOG 请直接访问仓库。

## 本目录内容

```
tdxdata/
├── LICENSE                  # 上游 MIT 许可证
├── VENDORED.md              # 本文件
├── __init__.py
├── client.py                # Client / 数据中心产品 / 行情接口
├── aio.py                   # AsyncClient
├── models.py / errors.py / enums.py
├── capabilities.py          # 能力边界注册表
├── indicator.py             # 11 个确定性指标
├── limits.py / symbols.py / calendar.py / master.py
├── sections/                # F10 / 财报 / 分红 / 板块 / 资金流语义层
├── _engine/                 # 7615 协议引擎（含 catalog JSON）
└── data/products.json       # 421 个数据产品目录
```

## 本地修改

**无。** 本目录保持上游原样，便于后续 diff/升级。
本项目的适配代码全部放在 `tradex/data_sources/tdxdata_fetchers.py` 与
`tradex/tools/tdxdata_data.py`（Phase 1），不直接改上游源码。

## 升级步骤

1. 从上游指定 commit 重新复制 `tdxdata/` 包目录（排除 `__pycache__`）；
2. 更新本文件的版本/commit/同步日期；
3. 运行离线测试：
   ```bash
   pytest tests/test_tdxdata_fetchers.py tests/test_tdxdata_tools.py -q
   ```
4. 运行真实网络冒烟：
   ```bash
   python scripts/tdxdata_smoke.py
   ```
5. 重点核对上游 `CHANGELOG.md` 中标注 **BREAKING** 的变更
   （例如 `vol` 单位、`1m` 周期语义、`meta` → `provenance`），
   并同步检查本项目的映射层。

## 注意

- 根 `.gitignore` 有一条 `data/` 规则会忽略本目录的 `data/products.json`，
  已在 `.gitignore` 中追加放行规则；不要删除。
- `tradex/pyproject.toml` 的 hatch wheel `packages` 已加入 `src/tdxdata`；
  构建后请确认 wheel 内含 `tdxdata/data/products.json` 与
  `tdxdata/_engine/catalog/*.json`，否则运行时会报
  `TdxError: 产品数据缺失`。
- 上游数据来自第三方公开服务器，代码为 MIT；数据合规性由使用方自行确认。
