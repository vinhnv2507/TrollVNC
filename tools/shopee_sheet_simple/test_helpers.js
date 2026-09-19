const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(__dirname + '/Code.gs', 'utf8').replace(/^\uFEFF/, '');
const ctx = { Utilities: {
  newBlob: bytes => ({ getDataAsString: () => Buffer.from(bytes).toString('utf8') }),
  formatDate: () => '17/09/2026 13:58'
} };
vm.createContext(ctx);
vm.runInContext(code, ctx);
function eq(actual, expected, label) {
  if (actual !== expected) throw new Error(`${label}: expected ${expected}, got ${actual}`);
}
eq(ctx.normalizeCookie_('Cookie: SPC_ST=abc; SPC_U=1'), 'SPC_ST=abc; SPC_U=1', 'normalize cookie');
eq(ctx.decodePossiblyHex_('4769616f206b69656e'), 'Giao kien', 'hex decode');
eq(ctx.extractOrderId_('/order?orderId=243166939278651&utm_source=x'), '243166939278651', 'order id');
eq(ctx.extractTracking_('Kiá»‡n hÃ ng SPXVN061002742949 Ä‘Ã£ giao'), 'SPXVN061002742949', 'tracking');
eq(ctx.extractOrderSn_('Ä‘Æ¡n hÃ ng 2609154UF5PF9U Ä‘Ã£ giao', ''), '2609154UF5PF9U', 'order sn');
eq(ctx.detectCarrier_('ÄÆ¡n giao bá»Ÿi GHN', {}), 'GHN', 'GHN carrier');
eq(ctx.detectCarrier_('Viettel Post Ä‘ang giao', {}), 'VIETTELPOST', 'Viettel Post carrier');
eq(ctx.extractTracking_('WAYBILL: GHNABC123456', 'GHN', {}), 'GHNABC123456', 'labelled tracking');
eq(ctx.extractTracking_('', 'GHN', { tracking_number: 'ghn123456' }), 'GHN123456', 'explicit tracking');
eq(ctx.translateShippingStatus_('Delivered'), 'Đã giao hàng thành công', 'translate delivered');
eq(ctx.formatShippingStatus_({ status: 'Delivered', carrier: 'SPX' }, ''), 'Đã giao hàng thành công | SPX', 'shipping status detail');
eq(ctx.formatShippingStatus_({ status: 'Delivered', carrier: 'SPX', actualTime: 1789628302 }, ''), 'Đã giao hàng thành công | SPX | 17/09/2026 13:58', 'shipping status with time');
const ref = ctx.extractItemReference_({}, { shop_item_id_list: [{ item_id: 123, shop_id: 456 }] }, {}, {}, {});
eq(ref.itemId, '123', 'item reference item');
eq(ref.shopId, '456', 'item reference shop');
const unsafe = ctx.extractItemReference_({}, {}, {}, { id_info: { itemid: null, shopid: 999 } }, {});
eq(unsafe.itemId, '', 'do not trust unpaired notification shop id');
const merged = ctx.mergeOrderData_({ tracking: 'SPX1', status: 'Delivered', raw: {} }, null, {}, {}, {}, { name: 'Sản phẩm thử', itemid: 123, shopid: 456 });
eq(merged.product, 'Sản phẩm thử', 'public item name');
eq(merged.productUrl, 'https://shopee.vn/product/456/123', 'public product link');
console.log('Simple sheet helper tests: OK');
