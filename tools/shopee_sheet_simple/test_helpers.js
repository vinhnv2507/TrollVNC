const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(__dirname + '/Code.gs', 'utf8').replace(/^\uFEFF/, '');
const ctx = { Utilities: { newBlob: bytes => ({ getDataAsString: () => Buffer.from(bytes).toString('utf8') }) } };
vm.createContext(ctx);
vm.runInContext(code, ctx);
function eq(actual, expected, label) {
  if (actual !== expected) throw new Error(`${label}: expected ${expected}, got ${actual}`);
}
eq(ctx.normalizeCookie_('Cookie: SPC_ST=abc; SPC_U=1'), 'SPC_ST=abc; SPC_U=1', 'normalize cookie');
eq(ctx.decodePossiblyHex_('4769616f206b69656e'), 'Giao kien', 'hex decode');
eq(ctx.extractOrderId_('/order?orderId=243166939278651&utm_source=x'), '243166939278651', 'order id');
eq(ctx.extractTracking_('Kiện hàng SPXVN061002742949 đã giao'), 'SPXVN061002742949', 'tracking');
eq(ctx.extractOrderSn_('đơn hàng 2609154UF5PF9U đã giao', ''), '2609154UF5PF9U', 'order sn');
eq(ctx.detectCarrier_('Đơn giao bởi GHN', {}), 'GHN', 'GHN carrier');
eq(ctx.detectCarrier_('Viettel Post đang giao', {}), 'VIETTELPOST', 'Viettel Post carrier');
eq(ctx.extractTracking_('WAYBILL: GHNABC123456', 'GHN', {}), 'GHNABC123456', 'labelled tracking');
eq(ctx.extractTracking_('', 'GHN', { tracking_number: 'ghn123456' }), 'GHN123456', 'explicit tracking');
console.log('Simple sheet helper tests: OK');
