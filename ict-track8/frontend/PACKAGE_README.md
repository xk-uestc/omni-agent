# 云数智询 (Cloud Data Intelligence) — `ui-update` 分支前端重构说明

本目录包含基于 `memory` 分支升级的 `ui-update` 前端代码（位于 `ict-track8/frontend/`），专门针对 **“中国电子杯”第三届高校 ICT 产教融合创新大赛 · 赛题八（多模态数据驱动的可解释精准问数/问答智能体）** 进行了去“AI 廉价感”的产品级体验重塑。

## 一、 核心设计升级亮点（对标 Apple / Claude / ChatGPT / Google NotebookLM）

1. **克制、温润的现代视觉体系（去除荧光紫与赛博黑黄配色）**：
   - 采用 Apple HIG 与 Claude 风格的暖白/宣纸灰浅色基底（`#F7F8FA` + `#FFFFFF`）与官方赛事 Logo **云数智询 (Cloud Data Intelligence)** 云端蓝（`#0066CC`）主色调，同时支持一键切换纯正碳黑深色模式（True Carbon Dark）。
2. **渐进式可解释推理链路（Claude Extended Thinking / ChatGPT o3 风格）**：
   - 将原本散乱的工具调用日志收敛为可折叠的 **全链路可解释推理时间轴**，清晰呈现：
     - **中级任务 1 & 2**：要素精准识别与口语别名标准化改写（如“帝都” → `北京市`）
     - **中级任务 3（决赛 10 分项）**：缺失维度拦截与 **交互式澄清选择卡**
     - **中级任务 4 & 5**：精准表定位（Schema Linking）与多表关联 NL2SQL
     - **中级任务 6 / 7 / 8 / 9**：低质量扫描件倾斜校正、多级目录重构、文档公式识别与闭环计算验算
3. **右侧可解释性证据工作台（Claude Artifacts / Google NotebookLM 风格）**：
   - 点击回答中的 `[SQL-1]`、`[DOC-2]`、`[公式-3]` 引用徽章，右侧抽屉即刻联动展示语法高亮 SQL、交互式 Schema 关联 SVG 拓扑图、PDF 原文高亮切片与公式计算器、以及 `memory` 分支的 **多轮短期/长期记忆槽位面板**。

## 二、 快速应用到 `ui-update` 分支

将解压后的文件放入 `omni-agent` 仓库根目录，执行：

```bash
bash setup-ui-update-branch.sh
```

或者手动执行：

```bash
git checkout memory
git checkout -b ui-update
cp -r ict-track8/frontend/* <你的仓库路径>/ict-track8/frontend/
git add ict-track8/frontend
git commit -m "feat(ui): redesign frontend UI for Cloud Data Intelligence"
git push -u origin ui-update
```
