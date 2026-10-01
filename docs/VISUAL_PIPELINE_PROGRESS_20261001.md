# 原页图证据与表格问数

本轮全部在独立D:/ICT8-OmniAgent实施，不依赖原项目服务。

## 已形成真实程序链路

1. 注册PDF原件固定SHA → 原页或裁剪RGB PNG → 原文件、页码、图SHA、原生词框与坐标manifest。
2. HTTP manifest与PNG再次核对来源和图SHA；统一鉴权、有限并发及渲染预算。
3. 资料页面与引用中的“查看页图证据”：同源授权读取、浏览器SHA核验、换页和请求取消。
4. 指定gpt-6-luna/medium Responses图像请求：最多两份本地资产，不接受任意路径/远程URL；审计仅标识、SHA、尺寸和usage，无图像base64或凭据。
5. 有线原生网格 → 独立行列/单元格提取 → 原问题唯一明确行列范围 → 视觉模型选择 → 服务器返回对应原字面值；邻行、换年、实际预测混用、不明确条件/计算不会静默变成单值回答。

相关接口：

- POST `/api/v1/knowledge/documents/{id}/visual-evidence`
- GET `/api/v1/knowledge/documents/{id}/pages/{page}/visual.png`
- POST `/api/v1/knowledge/documents/{id}/visual-table-query`

Body包含`page_no`、`expected_source_sha256`，可选`crop_display_pt`；问数增加`question`。

## 真实验收与限制

| 验收 | 结果 | 证据边界 |
|---|---|---|
| 图像独有随机数字/颜色位置 | 2/2；2API completed、1200 tokens | 两张raster图的PDF文字层为空，答案未作为文本输入；不是完整视觉准确率 |
| 单个随机原生网格问数 | 通过；1API completed、1015 tokens | West/2025 Units完整键及服务器6635一致；不是未知题或官方基准 |
| 全量本地回归 | 1117 passed、0 failed、1告警；47.70秒 | 不是模型准确率 |
| 绝对PDF坐标 | Rotate×CropBox对照原未裁物理位置通过 | 修复旧矩阵可往返但偏移40pt的错误；旧报告不改写 |

真实报告分别为`VISUAL_MODEL_PROBE_20261001T074739083879Z-8676b3ac.json`、`VISUAL_TABLE_LOOKUP_20261001T080201900680Z.json`。

原生网格只证明文字层与独立几何行列的字面关系，不证明隐藏文字层与可见像素完全一致或一般事实真值。当前仅单层内部表头、首列行键；合并、多层、跨页、无边框、部分裁剪、扫描件和图表不宣称已支持。数值单位尚未独立绑定，`calculator_input_eligible=false`，不能将其直接冒充精确融合计算输入。

## 下一步要求

保留已有原文字面与数字守卫，补OCR/可见像素核对、无边框表结构、图表系列/年份/单位关系及页级候选检索，再进行新的独立官方复测。本轮没有重跑OHR：其最新冻结12题仍3实质回答、8拒答、1回退，EM0/12。不能根据本轮小探针宣称官方多模态准确率达标或全赛题完成。
