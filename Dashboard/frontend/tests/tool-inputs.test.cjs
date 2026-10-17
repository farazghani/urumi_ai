const {test} = require('node:test');
const assert = require('node:assert/strict');
const {defaultArguments} = require('../tool-inputs.js');
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
  assert.deepEqual(defaultArguments({required:['order_id','status'],properties:{order_id:{type:'integer',minimum:1},status:{enum:['pending','completed']},optional:{type:'string'}}}),{order_id:1,status:'pending'});
});
