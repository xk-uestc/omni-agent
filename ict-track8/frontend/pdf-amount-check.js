(function(root){
  function describe(cell){
    const check=cell?.amount_check;if(!check||!['matched','conflict','needs_review','unavailable'].includes(check.status))return null;
    const labels={matched:'与原文字金额一致',conflict:'金额或币种冲突',needs_review:'金额需复核',unavailable:'金额未独立核验'};
    if(check.status==='unavailable'&&!check.ocr_value&&!check.native_value&&!/[$€£¥￥]|USD|EUR|GBP|CNY|RMB/.test(cell.text||''))return null;
    const color={matched:'#16803c',conflict:'#c62828',needs_review:'#a65f00',unavailable:'#737373'}[check.status];
    return {label:labels[check.status],color,detail:`${labels[check.status]}；原PDF文字：${check.native_text||'不可用'}；OCR：${check.ocr_text??cell.text??''}。原文字层本身仍可能错误。`};
  }
  const api={describe};if(typeof module!=='undefined'&&module.exports)module.exports=api;root.PdfAmountCheck=api;
})(typeof window!=='undefined'?window:globalThis);
