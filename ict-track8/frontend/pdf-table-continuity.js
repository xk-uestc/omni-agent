(function(root){
  function selections(start,end,table,firstHeader,nextHeader){
    if(![start,end,table,firstHeader,nextHeader].every(Number.isInteger)||start<1||end<start||end>1000||end-start>=4||table<1||table>32||firstHeader<0||firstHeader>5||nextHeader<0||nextHeader>5)
      throw Error('请选择连续1到4页、有效表格序号及0到5行表头。');
    return Array.from({length:end-start+1},(_,index)=>({page_no:start+index,table_index:table-1,header_rows:index?nextHeader:firstHeader}));
  }
  function appendLink(links,candidate){
    if(candidate.status!=='needs_confirmation')throw Error('这两页的表格尚不满足连接条件。');
    const edge={from_page:candidate.from_page,from_table:candidate.from_table,to_page:candidate.to_page,to_table:candidate.to_table};
    if(links.some(item=>Object.keys(edge).every(key=>item[key]===edge[key])))return links;
    if(links.some(item=>item.from_page===edge.from_page&&item.from_table===edge.from_table||item.to_page===edge.to_page&&item.to_table===edge.to_table))throw Error('已有其他续表选择，请重新预览后选择。');
    return [...links,edge];
  }
  const api={selections,appendLink};if(typeof module!=='undefined'&&module.exports)module.exports=api;root.PdfTableContinuity=api;
})(typeof window!=='undefined'?window:globalThis);
