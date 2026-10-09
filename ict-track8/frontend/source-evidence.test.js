const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function loadSourceEvidence(){
  const window={};
  const document={addEventListener(){}};
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'source-evidence.js'),'utf8'),{window,document,Set,Map,JSON,Math,Number,String,Array,Object,RegExp});
  return window.SourceEvidence;
}

const source={name:'web_traffic_daily',used_columns:['visit_date','device_type','page_views']};
function trafficResult(){
  return {source_tables:{tables:[source]},rows:[
    {device_type:'手机',网页浏览量:120},
    {device_type:'电脑',网页浏览量:240},
    {device_type:'平板',网页浏览量:60},
  ],plan:{table:source.name,metric_table:source.name,metric_column:'page_views',metric_function:'SUM',metric_label:'网页浏览量',
    filters:[{table:source.name,column:'visit_date',operator:'RANGE',value:['2025-01-01','2026-01-01']}],
    dimensions:['device_type'],dimension_tables:{device_type:source.name},dimension_labels:{device_type:'device_type'},
    dimension_transforms:{},metrics:[{id:'m0',table:source.name,column:'page_views',function:'SUM',label:'网页浏览量',filters:[]}],
    join_conditions:[],join_tables:[],comparison_mode:'none'},};
}

test('grouped annual metric locates output groups and highlights only qualifying source cells',()=>{
  const api=loadSourceEvidence(),result=trafficResult();
  const candidates=api.sourceFilterCandidates(result,source);
  assert.equal(candidates.length,3);
  assert.deepEqual(JSON.parse(JSON.stringify(candidates[0].map(item=>[item.column,item.operator,item.value]))),[
    ['visit_date','RANGE',['2025-01-01','2026-01-01']],['device_type','=','手机'],
  ]);
  assert.deepEqual([...api.selectedRowFields({visit_date:'2025-04-03',device_type:'手机',page_views:200},source,result)],
    ['visit_date','device_type','page_views']);
  assert.deepEqual([...api.selectedRowFields({visit_date:'2024-12-31',device_type:'手机',page_views:200},source,result)],[]);
  assert.deepEqual([...api.selectedRowFields({visit_date:'2026-01-01',device_type:'电脑',page_views:200},source,result)],[]);
  assert.deepEqual([...api.selectedRowFields({visit_date:'2025-04-03',device_type:'服务器',page_views:200},source,result)],[]);
});

test('Top-N and HAVING outputs constrain highlighted dimension groups',()=>{
  const api=loadSourceEvidence(),result=trafficResult();
  result.rows=result.rows.slice(0,2);result.plan.top_n=2;result.plan.having={operator:'>',mode:'scalar',value:100};
  assert.notEqual(api.selectedRowFields({visit_date:'2025-03-01',device_type:'手机',page_views:30},source,result).size,0);
  assert.notEqual(api.selectedRowFields({visit_date:'2025-03-01',device_type:'电脑',page_views:30},source,result).size,0);
  assert.equal(api.selectedRowFields({visit_date:'2025-03-01',device_type:'平板',page_views:30},source,result).size,0);
});

test('MIN and MAX mark only source values that equal the returned aggregate',()=>{
  const api=loadSourceEvidence(),table={name:'scores',used_columns:['team','score']};
  const result={source_tables:{tables:[table]},rows:[{team:'A',最高分:92}],plan:{table:'scores',metric_table:'scores',
    metric_column:'score',metric_function:'MAX',metric_label:'最高分',metrics:[{id:'m0',table:'scores',column:'score',function:'MAX',label:'最高分',filters:[]}],
    dimensions:['team'],dimension_tables:{team:'scores'},dimension_labels:{team:'team'},dimension_transforms:{},filters:[],join_conditions:[],join_tables:[],comparison_mode:'none'}};
  assert.deepEqual([...api.selectedRowFields({team:'A',score:92},table,result)],['team','score']);
  assert.deepEqual([...api.selectedRowFields({team:'A',score:88},table,result)],['team']);
});

test('COUNT(*) marks counted primary-key cells and does not mislabel a nullable metric as its input',()=>{
  const api=loadSourceEvidence(),table={name:'events',used_columns:['event_date','category']};
  const schema={columns:[{name:'event_id',primary_key:true},{name:'event_date',primary_key:false},
    {name:'category',primary_key:false},{name:'optional_ref',primary_key:false}]};
  const result={source_tables:{tables:[table]},rows:[{category:'A',记录数:2}],plan:{table:'events',metric_table:'events',
    metric_column:'optional_ref',metric_function:'COUNT',metric_label:'记录数',filters:[{table:'events',column:'event_date',operator:'=',value:'2025-01-01'}],
    dimensions:['category'],dimension_tables:{category:'events'},dimension_labels:{category:'category'},dimension_transforms:{},
    metrics:[{id:'m0',table:'events',column:'optional_ref',function:'COUNT',label:'记录数',filters:[]}],
    join_conditions:[],join_tables:[],comparison_mode:'none'}};
  const row={event_id:7,event_date:'2025-01-01',category:'A',optional_ref:null};
  assert.deepEqual([...api.selectedRowFields(row,table,result,schema)],['event_date','category','event_id']);
  assert.deepEqual(api.countIdentityColumns(result,table,schema),['event_id']);
  assert.deepEqual([...api.selectedRowFields({...row,event_date:'2024-12-31'},table,result,schema)],[]);
  assert.deepEqual([...api.selectedRowFields(row,table,result,{columns:schema.columns.filter(column=>!column.primary_key)})],
    ['event_date','category']);
});

test('unselected metrics stay unhighlighted while selected derived outputs retain their inputs',()=>{
  const api=loadSourceEvidence(),table={name:'facts',used_columns:['team','amount','cost']};
  const result={source_tables:{tables:[table]},rows:[{team:'A',amount:90}],plan:{table:'facts',dimensions:['team'],
    dimension_tables:{team:'facts'},dimension_labels:{team:'team'},dimension_transforms:{},filters:[],join_conditions:[],join_tables:[],comparison_mode:'none',
    metrics:[{id:'amount_metric',table:'facts',column:'amount',function:'SUM',label:'amount',filters:[]},
      {id:'cost_metric',table:'facts',column:'cost',function:'SUM',label:'cost',filters:[]}],derived_metrics:[],output_metrics:['amount_metric']}};
  assert.deepEqual([...api.selectedRowFields({team:'A',amount:90,cost:40},table,result)],['team','amount']);
  result.rows=[{team:'A',margin:0.5}];result.plan.derived_metrics=[{id:'margin',label:'margin',expression:{op:'divide',left:{ref:'amount_metric'},right:{ref:'cost_metric'}}}];
  result.plan.output_metrics=['margin'];
  assert.deepEqual([...api.selectedRowFields({team:'A',amount:90,cost:40},table,result)],['team','amount','cost']);
});

test('year and month dimensions create half-open source date ranges',()=>{
  const api=loadSourceEvidence(),table={name:'traffic',used_columns:['visit_date','views']};
  const result={source_tables:{tables:[table]},rows:[{year:2025,views:1}],plan:{table:'traffic',metric_table:'traffic',
    metric_column:'views',metric_function:'SUM',metric_label:'views',metrics:[{id:'m',table:'traffic',column:'views',function:'SUM',label:'views',filters:[]}],
    dimensions:['visit_date'],dimension_tables:{visit_date:'traffic'},dimension_labels:{visit_date:'year'},
    dimension_transforms:{visit_date:'year'},filters:[],join_conditions:[],join_tables:[],comparison_mode:'none'}};
  assert.deepEqual(JSON.parse(JSON.stringify(api.sourceFilterCandidates(result,table)[0].at(-1))),
    {table:'traffic',column:'visit_date',operator:'RANGE',value:['2025-01-01','2026-01-01']});
  assert.deepEqual([...api.selectedRowFields({visit_date:'2025-06-01',views:40},table,result)],['visit_date','views']);
  assert.deepEqual([...api.selectedRowFields({visit_date:'2026-01-01',views:40},table,result)],[]);
  result.rows=[{month:'2025-12',views:1}];result.plan.dimension_transforms.visit_date='month';
  result.plan.dimension_labels.visit_date='month';
  assert.deepEqual(JSON.parse(JSON.stringify(api.sourceFilterCandidates(result,table)[0].at(-1))),
    {table:'traffic',column:'visit_date',operator:'RANGE',value:['2025-12-01','2026-01-01']});
});

test('LIKE matching follows SQLite ASCII case folding without coloring unrelated Unicode values',()=>{
  const api=loadSourceEvidence(),table={name:'pages',used_columns:['campaign','views']};
  const result={source_tables:{tables:[table]},rows:[{views:2}],plan:{table:'pages',metric_table:'pages',metric_column:'views',
    metric_function:'SUM',metric_label:'views',metrics:[{id:'m',table:'pages',column:'views',function:'SUM',label:'views',filters:[]}],
    filters:[{table:'pages',column:'campaign',operator:'LIKE',value:'WEB-%'}],dimensions:[],join_conditions:[],join_tables:[],comparison_mode:'none'}};
  assert.notEqual(api.selectedRowFields({campaign:'web-spring',views:4},table,result).size,0);
  assert.equal(api.selectedRowFields({campaign:'wéb-spring',views:4},table,result).size,0);
});
