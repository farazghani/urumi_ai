/* Preserve JSON Schema defaults exactly, including null, false and zero. */
(function (root) {
  function sampleValue(schema) {
    if (Object.hasOwn(schema, 'default')) return schema.default;
    if (Object.hasOwn(schema, 'const')) return schema.const;
    if (schema.enum?.length) return schema.enum[0];
    if (schema.anyOf || schema.oneOf) {
      const alternatives = schema.anyOf || schema.oneOf;
      return sampleValue(alternatives.find(s => s.type !== 'null') || alternatives[0]);
    }
    if (schema.type === 'object') return defaultArguments(schema);
    if (schema.type === 'array') return [];
    if (schema.type === 'boolean') return false;
    if (schema.type === 'null') return null;
    if (schema.type === 'integer' || schema.type === 'number') {
      const minimum = schema.minimum ?? (schema.exclusiveMinimum !== undefined ? schema.exclusiveMinimum + 1 : 0);
      return schema.type === 'integer' ? Math.ceil(minimum) : minimum;
    }
    return '';
  }
  function defaultArguments(schema) {
    const args = {};
    for (const [name, field] of Object.entries(schema.properties || {})) {
      if (Object.hasOwn(field, 'default')) args[name] = field.default;
      else if ((schema.required || []).includes(name)) args[name] = name.endsWith('_id') ? null : sampleValue(field);
      // Omitted optional fields are left to the upstream's own defaults.
    }
    return args;
  }
  function orderArguments(schema, orders, selectFirst = true) {
    const args = defaultArguments(schema);
    const first = (orders || []).find(order => Number.isInteger(order?.id) && order.id > 0);
    args.order_id = selectFirst && first ? first.id : null;
    return args;
  }
  root.UrumiToolInputs = { defaultArguments, orderArguments };
  if (typeof module !== 'undefined') module.exports = root.UrumiToolInputs;
})(globalThis);
