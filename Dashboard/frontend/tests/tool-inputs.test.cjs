const {test} = require('node:test');
const assert = require('node:assert/strict');
const {defaultArguments,orderArguments} = require('../tool-inputs.js');
test('WooCommerce list arguments preserve optional null instead of invalid empty status', () => {
  const schema = {properties:{
    search:{anyOf:[{type:'string'},{type:'null'}],default:null},
    status:{anyOf:[{enum:['publish','draft','private','pending'],type:'string'},{type:'null'}],default:null},
    page:{type:'integer',minimum:1,default:1},
    per_page:{type:'integer',minimum:1,maximum:100,default:20}
  }};
  assert.deepEqual(defaultArguments(schema),{search:null,status:null,page:1,per_page:20});
});
test('defaults preserve zero, false, and empty strings; optional fields without defaults are omitted', () => {
  assert.deepEqual(defaultArguments({properties:{a:{default:0},b:{default:false},c:{default:''},optional:{type:'string'}}}),{a:0,b:false,c:''});
});
test('required fields use enum values and numeric bounds', () => {
  assert.deepEqual(defaultArguments({required:['quantity','status'],properties:{quantity:{type:'integer',minimum:1},status:{enum:['pending','completed']},optional:{type:'string'}}}),{quantity:1,status:'pending'});
});

test('record ID minimum is a constraint, never a guessed existing ID', () => {
  assert.deepEqual(defaultArguments({required:['order_id','product_id'],properties:{order_id:{type:'integer',minimum:1},product_id:{type:'integer',minimum:1}}}),{order_id:null,product_id:null});
});
test('order lookup uses real returned ID 17 instead of schema minimum 1', () => {
  const schema={required:['order_id'],properties:{order_id:{type:'integer',minimum:1}}};
  assert.deepEqual(orderArguments(schema,[{id:17}]),{order_id:17});
  assert.deepEqual(orderArguments(schema,[]),{order_id:null});
  assert.deepEqual(orderArguments(schema,[{id:'invalid'},{id:0},{id:17}]),{order_id:17});
});
test('write tools require explicit order selection', () => {
  const schema={required:['order_id','status'],properties:{order_id:{type:'integer',minimum:1},status:{enum:['pending','completed']}}};
  assert.deepEqual(orderArguments(schema,[{id:17}],false),{order_id:null,status:'pending'});
});
