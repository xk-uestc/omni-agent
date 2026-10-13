(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.DocumentSummary=api;})(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';
  const count=value=>Number.isInteger(value)&&value>=0?value:null;
  const pageLabel=pages=>pages.length?`第 ${pages.join('、')} 页`:'';
  function summarize(document={}){
    const stats=document.stats||{},warnings=Array.isArray(document.warnings)?document.warnings:[],metrics=[];
    const add=(label,value)=>{if(count(value)!==null)metrics.push({label,value});};
    if(document.modality==='pdf')add('页',stats.page_count);
    if(document.modality==='xlsx')add('工作表',stats.visible_sheet_count);
    if(document.modality==='pdf')add('原生表格',stats.native_table_count);
    else add('表块',stats.table_count);
    add('公式片段',stats.formula_count);add('索引片段',document.chunk_count);
    const issues=[],seen=new Set();
    const issue=(message,action,kind='warning')=>{if(!seen.has(message)){seen.add(message);issues.push({message,action,kind});}};
    const failed=Array.isArray(stats.ocr_failed_pages)?stats.ocr_failed_pages:[];
    const pending=Array.isArray(stats.ocr_required_pages)?stats.ocr_required_pages:[];
    if(failed.length)issue(`${pageLabel(failed)} 的文字识别未完成。`,'上传更清晰的扫描件，或先查询其他可读页面。');
    if(pending.length)issue(`${pageLabel(pending)} 仍缺少可用文字。`,'建议上传带文字层的 PDF，或更清晰的原文件。');
    if(warnings.some(x=>String(x).startsWith('native_table_ingest_page_budget')))
      issue('文件超过本次原生表格解析的页数范围。','文字仍可检索；精确表格问数时请选择目标页。');
    if(warnings.some(x=>String(x).startsWith('native_table_ingest_time_budget')))
      issue('原生表格解析已达到处理时限。','文字仍可检索；可拆分目标页后重新上传。');
    for(const warning of warnings){
      const text=String(warning),prefix=text.replace(/^page_\d+:/,'');
      if(/ocr_status:ok|page_orientation_detected|native_table_ingest_(page|time)_budget/.test(prefix))continue;
      if(/header_ambiguous|inherited_header|table_continuation_unverified/.test(prefix))issue('部分表头或跨页关系需要核对。','先核对原表行列；Excel 可在下方高级工具中指定表头。');
      else if(/formula_cache/.test(prefix))issue('Excel 公式缓存尚未核验。','请用表格软件重新计算并保存文件，再上传用于计算。');
      else if(/ocr_not_configured/.test(prefix))issue('当前服务未启用图片文字识别。','请上传带文字层的 PDF 或文本，或联系部署人员启用 OCR。');
      else if(/ocr_low_confidence/.test(prefix))issue('部分文字的识别置信度较低。','建议打开原件核对数字与专有名词，或重新上传清晰扫描件。');
      else if(/ocr_required|ocr_page_limit|ocr_failed|ocr_returned_no_text|ocr_status:(?!ok)|ocr_unavailable/.test(prefix))issue('部分页面的文字识别仍需补充。','请核对原件，必要时上传清晰的目标页。');
      else if(/structured_recognition_requires_review|parser_observation_requires_review/.test(prefix))issue('检测到的结构还需要来源核对。','问答时会继续核验原文，识别结果不会直接视为已验证事实。');
      else if(/hidden_|merged_cells|repeated_header_removed|section_header_inferred/.test(prefix))issue('表格结构已保留，部分布局经过整理。','可在高级表格工具中核对隐藏区域、合并单元格和表头。','info');
      else if(/cell_error/.test(prefix))issue('部分单元格包含表格错误值。','修正原表中的错误后重新上传；这些值不能用于计算。');
      else if(/ocr_review_recommended|repeated_page_matter_removed|ocr_result_rejected_native_text_preserved/.test(prefix))issue('部分识别内容建议人工核对。','可打开原文件核对对应文字。','info');
      else issue('这份资料有解析提示。','展开技术详情查看记录，并核对原文件。');
    }
    const chunks=count(document.chunk_count),imageChunks=count(stats.content_type_breakdown?.image)||0;
    const readable=chunks!==null&&chunks>imageChunks;
    if(chunks!==null&&!readable)issue('当前没有可检索的文本片段。','请检查文件是否为空、是否加密，或上传可读版本。');
    const review=issues.some(x=>x.kind==='warning');
    return {metrics,issues,readable,status:!readable?'暂无可检索内容':review?'已入库 · 部分待核对':'已入库 · 可提问',
      tone:!readable||review?'warning':'ready',
      ocrPages:Array.isArray(stats.ocr_succeeded_pages)?stats.ocr_succeeded_pages:[],
      formulaNote:'公式片段表示解析器发现含公式的文本，不等于已验证可计算公式。'};
  }
  function friendlyError(error){
    const raw=typeof error==='string'?error:error?.message||'';
    if(/Failed to fetch|NetworkError|Load failed/i.test(raw))return '暂时无法连接服务。请检查连接后重试，已选文件会保留。';
    if(/20\s*MiB|20\s*MB|28_000_000|28000000|too large/i.test(raw))return '文件超过 20 MiB。请压缩文件或拆分需要分析的页面后上传。';
    if(/加密|encrypted/i.test(raw))return '这个 PDF 已加密。请先导出可读取的版本后上传。';
    if(/PDF 无法解析|invalid pdf|broken document/i.test(raw))return '无法读取这个 PDF。请确认文件完整，或重新导出为 PDF 后上传。';
    if(/解析繁忙|429/.test(raw))return '当前正在解析其他资料，请稍后重试。';
    if(/不支持的文档格式|UnicodeDecodeError|utf-8/i.test(raw))return '无法读取这个文件格式。请使用支持的格式；文本文件请保存为 UTF-8。';
    if(/HTTP 5\d\d|Internal Server Error/i.test(raw))return '服务暂时未完成解析。请稍后重试，若持续失败可尝试拆分目标页面。';
    return raw||'资料未上传成功，请检查文件后重试。';
  }
  return {summarize,friendlyError};
});
