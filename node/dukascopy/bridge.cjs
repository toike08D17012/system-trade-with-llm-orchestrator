'use strict';
// Offline only: all physical sends belong to the Python coordinator.
globalThis.fetch = () => { throw new Error('network_forbidden'); };
const net = require('node:net');
net.Socket.prototype.connect = () => { throw new Error('network_forbidden'); };
const fs = require('node:fs');
const lib = require('dukascopy-node');
const version = require('dukascopy-node/package.json').version;
const DAY = 86400000;
function run(input) {
  if (version !== '1.50.0') throw new Error('package_version_mismatch');
  let result;
  if (input.command === 'urls') {
    const startDate = new Date(input.start), endDate = new Date(input.end), nowDate = new Date(input.now);
    if (![startDate,endDate,nowDate].every(d => Number.isFinite(+d)) || startDate >= endDate || endDate > nowDate)
      throw new Error('invalid_period');
    result = lib.generateUrls({instrument:'usdjpy',timeframe:'d1',priceType:'bid',startDate,endDate,nowDate});
  } else if (input.command === 'decode') {
    const raw = JSON.parse(input.body);
    if (raw.shift !== DAY || !Number.isSafeInteger(raw.timestamp) || raw.timestamp % DAY !== 0 ||
        raw.multiplier !== 0.001 || !Array.isArray(raw.times) || !raw.times.length || raw.times.length > 366)
      throw new Error('invalid_daily_response');
    for (const field of ['opens','highs','lows','closes','volumes']) {
      if (!Array.isArray(raw[field]) || raw[field].length !== raw.times.length ||
          !raw[field].every(value => Number.isFinite(value) && (field === 'volumes' ? value >= 0 : Number.isSafeInteger(value)))) throw new Error('invalid_columns');
    }
    let timestamp = raw.timestamp;
    const actual = new Set();
    raw.times.forEach((delta,i) => {
      if (!Number.isSafeInteger(delta) || delta < (i ? 1 : 0) || delta > 366) throw new Error('invalid_delta');
      timestamp += delta * DAY; actual.add(timestamp);
    });
    if (!/^https:\/\/jetta\.dukascopy\.com\/v1\/candles\/day\/USD-JPY\/BID(?:\/\d{4}|\?from=\d+)$/.test(input.url))
      throw new Error('invalid_url');
    const processedData = lib.processData({requestedTimeframe:'d1',priceType:'bid',volumes:true,
      volumeUnits:'millions',ignoreFlats:false,bufferObjects:[{url:input.url,buffer:Buffer.from(input.body)}]});
    result = lib.formatOutput({processedData,format:'json',timeframe:'d1'}).filter(row => actual.has(row.timestamp));
    if (result.length !== actual.size) throw new Error('missing_actual_candle');
    for (const row of result) {
      if (![row.open,row.high,row.low,row.close].every(value => Number.isFinite(value) && value > 0) ||
          row.low > Math.min(row.open,row.close) || row.high < Math.max(row.open,row.close) || row.low > row.high)
        throw new Error('invalid_ohlc');
    }
  } else throw new Error('invalid_command');
  return {package_version:version,node_version:process.version,bridge_version:1,result};
}
module.exports = {run};
if (require.main === module) {
  try { process.stdout.write(JSON.stringify(run(JSON.parse(fs.readFileSync(0,'utf8'))))); }
  catch { process.stderr.write('dukascopy_bridge_failed\n'); process.exitCode = 1; }
}
