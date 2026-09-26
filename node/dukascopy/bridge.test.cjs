'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {run} = require('./bridge.cjs');
const DAY = 86400000;
const raw = {timestamp:Date.UTC(2026,0,1),shift:DAY,multiplier:0.001,
 open:150,high:151,low:149,close:150,times:[0,2],
 opens:[0,1],highs:[0,1],lows:[0,1],closes:[1,2],volumes:[1,2]};
const decode = body => run({command:'decode',body:JSON.stringify(body),url:'https://jetta.dukascopy.com/v1/candles/day/USD-JPY/BID/2026'});
test('year buckets, independent of trading day count', () => {
 assert.equal(run({command:'urls',start:'2023-09-26',end:'2026-09-26',now:'2026-09-26'}).result.length,4);
 assert.equal(run({command:'urls',start:'2026-09-24',end:'2026-09-26',now:'2026-09-26'}).result.length,1);
});
test('only original candles survive; never fill the missing day', () => {
 const result = decode(raw).result;
 assert.equal(result.length,2);
 assert.equal(result[1].timestamp,raw.timestamp+2*DAY);
 assert.equal(result[1].close,150.003);
});
test('reject malformed daily responses', () => {
 for (const change of [{shift:60000},{times:[]},{times:[0,0]},{closes:[null,1]},{multiplier:1}])
  assert.throws(() => decode({...raw,...change}));
});
test('disable direct network use', () => {
 assert.throws(() => fetch('https://example.com'), /network_forbidden/);
 assert.throws(() => require('node:net').connect(443,'example.com'), /network_forbidden/);
});
