// Anonymous editable contest presentation, using current on-disk evidence.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
const packageRoot = process.env.ICT8_ARTIFACT_PACKAGES || 'C:/Users/lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
process.env.RUNTIME_NODE_MODULES = packageRoot;
process.env.RUNTIME_NODE = process.execPath;
const packageRequire = createRequire(path.join(packageRoot,'ict8-resolver.cjs'));
const { Presentation, PresentationFile, FileBlob } = await import(pathToFileURL(packageRequire.resolve('@oai/artifact-tool')).href);

const root = path.resolve(import.meta.dirname, '..');
const skill = 'C:/Users/lenovo/.codex/plugins/cache/openai-primary-runtime/presentations/26.929.10730/skills/presentations';
const runtimePython = 'C:/Users/lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe';
const build = path.join(root, 'runtime/presentation-build');
const final = path.join(root, 'delivery/05-anonymous-defense.pptx');
await fs.mkdir(build, {recursive:true});
// This host's E: filesystem rejects the finalizer's atomic rename. Validate
// inside a native NTFS temporary workspace, then copy the checked file intact.
const validationWorkspace = process.env.ICT8_PRESENTATION_WORKSPACE || 'C:/Users/lenovo/.codex/tmp/ict8-presentation-20261001-v3';
await fs.mkdir(validationWorkspace,{recursive:true});
const { finalizePresentation, resolvePresentationFont, applyPresentationChartFont } =
  await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')).href);
const font = resolvePresentationFont({fontFamily:'Microsoft YaHei'});
const read = async name => JSON.parse(await fs.readFile(path.join(root,'docs',name),'utf8'));
const hybrid = (await read('HYBRID_ACCEPTANCE_REPORT.json')).summary;
const rag = await read('RAG_SCALE_REPORT.json');
const chinook = await read('CHINOOK_RULES_REPORT.json');
const regression = await read('LOCAL_REGRESSION_REPORT.json');
const faults = await read('FAULT_RECOVERY_REPORT.json');
if(!regression.ok || faults.passed!==faults.total) throw new Error('Current regression or fault audit failed');
const large=rag.records.find(r=>r.pages===500).global_semantic_query;
const p = Presentation.create({slideSize:{width:1280,height:720}});
const navy='#162B3C', blue='#176B87', gray='#526371';

function text(slide, value, x, y, w, h, size=30, color=navy, bold=false) {
  const s=slide.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
  s.text=value;
  s.text.style={typeface:font,fontSize:size,bold,color,autoFit:'none'};
  return s;
}
function slide(title, note, dark=false) {
  const s=p.slides.add(); s.background.fill=dark?navy:'#FFFFFF';
  if(title) text(s,title,72,45,1136,94,44,dark?'#FFFFFF':navy,true);
  s.speakerNotes.textFrame.setText(note);
  return s;
}
function rows(s, items) {
  items.forEach((a,i)=>{
    text(s,a[0],76,168+i*104,310,72,29,blue,true);
    text(s,a[1],410,168+i*104,790,80,28,navy);
  });
}
function table(s,values,widths,y=165,h=335) {
  const t=s.tables.add({rows:values.length,columns:values[0].length,left:76,top:y,width:1128,height:h,columnWidths:widths,values});
  t.borders.assign({style:'solid',fill:'#DFE6EB',width:1});
  for(let r=0;r<values.length;r++) for(let c=0;c<values[0].length;c++) {
    const cell=t.getCell(r,c);
    cell.fill=r===0?navy:r%2?'#F3F7F9':'#FFFFFF';
    cell.text.style={typeface:font,fontSize:24,bold:r===0,color:r===0?'#FFFFFF':navy};
  }
  return t;
}

let s=slide('', '依据：specification/official-track8.pdf；匿名自定义版式，组委会模板未提供。', true);
text(s,'OmniAgent',76,148,1128,115,66,'#FFFFFF',true);
text(s,'多模态数据驱动的\n可解释精准问数问答智能体',76,300,1128,168,46,'#FFFFFF');
text(s,'ICT 赛题八 · 2026-10-01',76,570,1128,54,25,'#CADAE4');

s=slide('赛题目标与当前实现','依据：官方赛题基础/中级/决赛任务；docs/ACCEPTANCE.md。');
rows(s,[['数据库问数','Schema链接、要素识别、澄清、JOIN与只读执行'],['多模态问答','六种格式、真实OCR、本地BGE与BM25融合'],['跨源计算','公式与数据绑定，核验单位、年份与政策版本'],['可解释交互','实际SQL、引用位置、依赖链与五轮会话']]);

s=slide('系统架构与共享证据契约','依据：docs/CURRENT_ARCHITECTURE.md；backend/omni_agent.py、dependency_agent.py。');
function node(value,x,y,w,h) {
  const a=s.shapes.add({geometry:'rect',position:{left:x,top:y,width:w,height:h},fill:'#F1F6F8',line:{fill:blue,width:1.5}});
  a.text=value;a.text.style={typeface:font,fontSize:27,color:navy,autoFit:'none'};return a;
}
const entry=node('用户问题 → 结构化会话 → 路由与工具计划',76,162,1128,68);
const branches=[node('结构化数据\nSchema / 值索引\n安全SQL / 只读执行',76,305,320,159),
  node('非结构化数据\n解析 / OCR\nBGE + BM25 + RRF',480,305,320,159),
  node('融合执行\n有界DAG / 前步引用\n公式 / 单位 / 年份',884,305,320,159)];
const finish=node('来源核验 → 最终答案 / 主动澄清 / 明确失败',76,544,1128,68);
for(const branch of branches) {
  s.shapes.connect(entry,branch,{kind:'elbow',fromSide:'bottom',toSide:'top',line:{fill:blue,width:2},tail:{type:'arrow',width:'med',length:'med'}});
  s.shapes.connect(branch,finish,{kind:'elbow',fromSide:'bottom',toSide:'top',line:{fill:blue,width:2},tail:{type:'arrow',width:'med',length:'med'}});
}

s=slide('NL2SQL：结构化计划与安全执行','依据：backend/nl2sql/；docs/CHINOOK_RULES_REPORT.json、SCHEMA_SCALE_REPORT.json。');
rows(s,[['字段与值共同链接','显式字段优先；Unicode归一化与值词边界'],['JOIN粒度核验','复合键、关系路径与防扇出；歧义进入澄清'],['计划经过统一安全门','模型不能执行裸SQL；参数化与只读AST验证'],['开发实测','Chinook 12/12；11/40/80表干扰9/9']]);

s=slide('多模态入库与可回看的来源','依据：samples/manifest.json、backend/knowledge_store.py、ocr.py。');
rows(s,[['15份实际样本','六种格式，自建合成CC0；不使用客户私有资料'],['结构定位','页、行、单元格、OCR区域；PDF目录首次2/8修复后8/8'],['真实OCR','RapidOCR ONNX CPU；三次以内恢复尝试'],['原文件核验','检索/公式命中后重查SHA；篡改或缺失停止']]);

s=slide('混合检索与编号约束','依据：backend/dense_retrieval.py、cross_source.py；docs/RAG_SCALE_REPORT.json。');
rows(s,[['BGE + BM25 + RRF','真实本地向量与词匹配融合，保留各路排名'],['编号限定证据范围','CASE0005不能命中CASE00050；缺失编号拒答'],['引用与数字核验','原文存在不等于完整语义蕴含，保持明确边界'],['阈值仍需独立校准','开发门槛不作为公开泛化准确率证明']]);

s=slide('跨源预测的可计算证据','依据：docs/HYBRID_ACCEPTANCE_REPORT.json；formula_binding.py、unit_algebra.py。');
table(s,[['来源','取值或公式','核验条件'],['文档','预测 = 基准 × (1 + 增长率)','明确基准年与目标年'],['SQL','2025华东销售额 29584','全年范围与金额单位'],['Excel','2026适用增长率 12%','参数适用年份'],['计算','2026预测 33134.08','来源、尺度与AST均有效']], [250,525,353],160,380);
text(s,'合成开发例子；年份或币种冲突时停止输出最终结论',76,587,1128,58,25,gray);

s=slide('五类跨源流程与实际依赖','依据：HYBRID_ACCEPTANCE_REPORT.json；dependency_agent.py。');
table(s,[['场景','数据依赖','校验'],['客单价','文档公式 / SQL销售额与订单数','来源 / 零分母'],['预测','PDF公式 / SQL基准 / Excel参数','年份 / 单位'],['地区问数','Excel地区作为SQL过滤','单元格 / 实体'],['冠军经验','SQL排名的实体用于文档检索','实体 / 引用'],['阈值比较','检索 / 来源事实 / Excel / le比较','原文 / 单位']], [240,588,300],157,430);
text(s,'五流程工具计划已验收；未知自然语言模型规划仍待实测',76,610,1128,54,24,gray);

s=slide('中级任务：问数侧','依据：官方九项中级任务；docs/ACCEPTANCE.md M01-M05。');
rows(s,[['要素识别与改写','NFKC、别名、值词边界；文本扰动5/5'],['跨域与低示例','Chinook 12/12；差旅新Schema首次6/8修复后8/8'],['主动澄清','指标/角色、时间与趋势粒度；刷新后继续追问'],['复杂结构','数十表链接、JOIN、聚合、嵌套与粒度核验']]);

s=slide('中级任务：文档与计算侧','依据：docs/ACCEPTANCE.md M06-M09；ROBUSTNESS_REPORT.json。');
rows(s,[['文档公式绑定','AST计算，来源参数、单位尺度与年份守卫'],['非标准目录','多种标题层级；行号与层级跳跃告警'],['复杂度路由','实际PageSignal；复杂路径900字 / 80字重叠'],['低质量恢复','保留原图候选；置信度选优仍是启发式']]);

s=slide('开发验收与真实模型状态','依据：HYBRID_ACCEPTANCE_REPORT.json、CHINOOK_RULES_REPORT.json、MODEL_API_PROBE.json。');
table(s,[['评测','结果','范围'],['问数',`${hybrid.sql_pass}/${hybrid.sql_total}`,'规则，合成开发题'],['文档问答',`${hybrid.qa_pass}/${hybrid.qa_total}`,'混合检索 + 摘录引用'],['融合',`${hybrid.fusion_pass}/${hybrid.fusion_total}`,'明确工具计划'],['公开Chinook',`${chinook.passed}/${chinook.total}`,'人工开发题，非标准基准'],['gpt-6-luna','HTTP 401','未获得真实模型成绩']], [340,230,558],160,420);
text(s,'开发通过率不能换算官方分数；回退不算真实模型通过',76,612,1128,54,24,gray);

s=slide('OCR成对扰动对照','依据：docs/ROBUSTNESS_REPORT.json；十种合成扰动，不是OHR-Bench。');
const ocr=await read('ROBUSTNESS_REPORT.json');
const baseline=ocr.ocr.filter(x=>x.baseline_correct).length, enhanced=ocr.ocr.filter(x=>x.pipeline_correct).length;
let chart=s.charts.add('bar',{position:{left:110,top:175,width:1050,height:355},categories:['原图识别','有界恢复'],series:[{name:'关键事实正确样本数',values:[baseline,enhanced],fill:blue}],hasLegend:false,barOptions:{direction:'column',grouping:'clustered'},dataLabels:{showValue:true,position:'outEnd',textStyle:{fontSize:27}},xAxis:{textStyle:{fontSize:24}},yAxis:{min:0,max:12,majorUnit:2,numberFormatCode:'0',textStyle:{fontSize:23}}});
applyPresentationChartFont(chart,{fontFamily:font});
text(s,'同一组10种合成扰动；只证明本组关键事实恢复，不能外推',76,579,1128,64,26,gray);

s=slide('文档规模与全局语义延迟','依据：docs/RAG_SCALE_REPORT.json；文字PDF在线混合摘录，不含OCR与外部生成。');
table(s,[['页数','切片数','热P50 / ms','热P95 / ms'],...rag.records.map(r=>[String(r.pages),String(r.chunks),String(r.global_semantic_query.warm.p50_ms),String(r.global_semantic_query.warm.p95_ms)])], [240,240,324,324],172,275);
text(s,`500页首次全局回答 ${(large.first_answer_ms/1000).toFixed(3)} 秒`,76,493,1128,60,34,blue,true);
text(s,'首次全局编码与热缓存单列，非新进程完整冷启动',76,580,1128,65,29,gray);

s=slide('五轮会话与实际故障恢复','依据：FAULT_RECOVERY_FIRST_RUN.json、FAULT_RECOVERY_REPORT.json、LOCAL_REGRESSION_REPORT.json。');
rows(s,[['五轮硬重启','kill子服务后恢复：29584 / 22992 / 4998 / 1 / 1'],['澄清与会话锁','pending选项跨重启；锁定503，解锁保留历史'],['故障实际审计',`相同十项首次4/10，修复后${faults.passed}/${faults.total}；不改主服务`],['完整本地回归',`${regression.passed}项通过；源文件、缺库、锁定失败均停止后步`]]);

s=slide('实用创新与转化价值','依据：公式/单位/日期守卫与公开资产验包；不申报已证实的SOTA领先。');
rows(s,[['可计算证据契约','公式、参数、时间与量纲共同决定能否计算'],['可核验的执行链','依赖来自实际工具引用；失败原因与原文可回看'],['独立可复现交付','公开权重、Schema、样本、许可与精确依赖'],['价值证据边界','尚无真实用户收益；不虚构节省工时或商业回报']]);

s=slide('后续验收与提交门槛','依据：docs/ACCEPTANCE.md；官方决赛交付要求。');
rows(s,[['真实模型','有效gpt-6-luna鉴权、通用多跳与跨源五轮'],['独立效果','冻结保留题；未知Schema与真实低质量文档'],['端到端效率','记录模型token成本、冷/热和故障恢复'],['正式交付','官方模板迁移、匿名校验、完整资产新目录验包']]);

const candidate=path.join(validationWorkspace,'candidate.pptx');
const checkedFinal=path.join(validationWorkspace,'output/anonymous-defense.pptx');
await fs.mkdir(path.dirname(checkedFinal),{recursive:true});
await (await PresentationFile.exportPptx(p)).save(candidate);
await finalizePresentation({workspaceDir:validationWorkspace,candidatePath:candidate,finalPath:checkedFinal,
  pythonExecutable:runtimePython,
  integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit',
    ...[7,8,11,13].flatMap(n=>['--require-native-table-slide',String(n)])],
  explicitTotalSlideCount:16,requiredNativeTableOwnerSlides:[7,8,11,13],requiredNativeChartOwnerSlides:[12],
  materializeLiteralChartWorkbooks:true,fontPolicy:{basis:'design',families:[font]},verifyArtifactToolImport:true,
  receiptPath:path.join(validationWorkspace,'validation.json')});
await fs.copyFile(checkedFinal,final);
await fs.copyFile(path.join(validationWorkspace,'validation.json'),path.join(build,'validation.json'));
const finalPresentation=await PresentationFile.importPptx(await FileBlob.load(final));
for(let i=0;i<finalPresentation.slides.items.length;i++) {
  const image=await finalPresentation.export({slide:finalPresentation.slides.items[i],format:'png',scale:1});
  await fs.writeFile(path.join(build,`slide-${String(i+1).padStart(2,'0')}.png`),new Uint8Array(await image.arrayBuffer()));
}
console.log(JSON.stringify({output:final,slides:p.slides.items.length,font}));
