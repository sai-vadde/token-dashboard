// charts.js — themed ECharts wrappers.
// Colors come from the CSS custom properties in style.css (:root) so the
// charts, badges, and everything else share a single validated palette.

const _css = getComputedStyle(document.documentElement);
const _v = (name, fallback) => (_css.getPropertyValue(name).trim() || fallback);

// Categorical slots — fixed order, never cycled past the end (CVD-validated).
const PALETTE = [
  _v('--chart-1', '#3987E5'), _v('--chart-2', '#199E70'), _v('--chart-3', '#C98500'),
  _v('--chart-4', '#9085E9'), _v('--chart-5', '#E66767'), _v('--chart-6', '#D55181'),
  _v('--chart-7', '#D95926'),
];

// Named roles for callers, so route files never hardcode hex.
export const CHART = {
  series: PALETTE,
  primary: _v('--chart-1', '#3987E5'),
  input: _v('--tok-input', '#86B6EF'),
  output: _v('--tok-output', '#3987E5'),
  cacheCreate: _v('--tok-cache-create', '#1C5CAB'),
  cacheRead: _v('--tok-cache-read', '#199E70'),
};

const INK = _v('--text', '#E7EAEE');
const MUTED = _v('--muted', '#98A0AC');
const GRID = _v('--grid', '#232830');
const SURFACE = _v('--panel', '#12151A');
const SURFACE_BORDER = _v('--border-2', '#2E343E');

const BASE = {
  textStyle: { color: INK, fontFamily: 'Inter' },
  color: PALETTE,
  grid: { left: 36, right: 12, top: 24, bottom: 24, containLabel: true },
};

const X_AXIS = {
  axisLine:  { lineStyle: { color: GRID } },
  axisLabel: { color: MUTED },
  axisTick:  { show: false },
};

const Y_AXIS = {
  axisLine:  { show: false },
  axisTick:  { show: false },
  splitLine: { lineStyle: { color: GRID } },
  axisLabel: { color: MUTED },
};

const LEGEND = {
  textStyle: { color: MUTED }, top: 0, right: 0,
  icon: 'roundRect', itemWidth: 8, itemHeight: 8,
};

const TOOLTIP = {
  trigger: 'axis',
  backgroundColor: SURFACE,
  borderColor: SURFACE_BORDER,
  borderWidth: 1,
  textStyle: { color: INK, fontFamily: 'Inter', fontSize: 12 },
  padding: [8, 12],
};

function mount(el) {
  const c = echarts.init(el, null, { renderer: 'svg' });
  window.addEventListener('resize', () => c.resize());
  return c;
}

export function lineChart(el, { x, series }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: TOOLTIP,
    legend: LEGEND,
    xAxis: { ...X_AXIS, type: 'category', data: x, boundaryGap: false },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map(s => ({
      ...s, type: 'line', smooth: true, showSymbol: false,
      areaStyle: { opacity: 0.12 }, lineStyle: { width: 2 },
    })),
  });
  return c;
}

export function barChart(el, { categories, values, color }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: { ...TOOLTIP, axisPointer: { type: 'shadow' } },
    xAxis: { ...X_AXIS, type: 'category', data: categories, axisLabel: { ...X_AXIS.axisLabel, interval: 0, rotate: categories.length > 5 ? 25 : 0 } },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: [{
      type: 'bar', data: values,
      itemStyle: { color: color || PALETTE[0], borderRadius: [4, 4, 0, 0] },
      barMaxWidth: 32,
    }],
  });
  return c;
}

export function stackedBarChart(el, { categories, series, formatter }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      axisPointer: { type: 'shadow' },
      valueFormatter: formatter || (v => Number(v).toLocaleString()),
    },
    legend: LEGEND,
    xAxis: {
      ...X_AXIS, type: 'category', data: categories,
      axisLabel: { ...X_AXIS.axisLabel, interval: categories.length > 20 ? 'auto' : 0, rotate: categories.length > 12 ? 45 : 0 },
    },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map((s, i) => ({
      name: s.name,
      type: 'bar',
      stack: 'total',
      data: s.values,
      // 2px surface-colored gap between segments so the stack reads as parts.
      itemStyle: { color: s.color || PALETTE[i % PALETTE.length], borderColor: SURFACE, borderWidth: 1 },
      barMaxWidth: 24,
      emphasis: { focus: 'series' },
    })),
  });
  return c;
}

export function groupedBarChart(el, { categories, series, formatter }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      axisPointer: { type: 'shadow' },
      valueFormatter: formatter || (v => Number(v).toLocaleString()),
    },
    legend: LEGEND,
    xAxis: {
      ...X_AXIS, type: 'category', data: categories,
      axisLabel: { ...X_AXIS.axisLabel, interval: 0, rotate: categories.length > 5 ? 25 : 0 },
    },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map((s, i) => ({
      name: s.name,
      type: 'bar',
      data: s.values,
      itemStyle: { color: s.color || PALETTE[i % PALETTE.length], borderRadius: [4, 4, 0, 0] },
      barMaxWidth: 24,
      emphasis: { focus: 'series' },
    })),
  });
  return c;
}

export function donutChart(el, data) {
  const c = mount(el);
  c.setOption({
    color: PALETTE,
    tooltip: {
      trigger: 'item',
      backgroundColor: SURFACE, borderColor: SURFACE_BORDER, borderWidth: 1,
      textStyle: { color: INK, fontFamily: 'Inter' },
      formatter: p => `${p.name}<br/><b>${Number(p.value).toLocaleString()}</b> tokens (${p.percent.toFixed(1)}%)`,
    },
    legend: {
      textStyle: { color: MUTED },
      bottom: 10, icon: 'roundRect', itemWidth: 8, itemHeight: 8,
      type: 'scroll',
    },
    series: [{
      type: 'pie',
      center: ['50%', '44%'],
      radius: ['48%', '68%'],
      avoidLabelOverlap: true,
      padAngle: 2,
      itemStyle: { borderColor: SURFACE, borderWidth: 2, borderRadius: 4 },
      label: {
        show: true,
        position: 'inside',
        color: '#fff',
        fontSize: 12,
        fontWeight: 600,
        formatter: ({ percent }) => percent >= 6 ? percent.toFixed(0) + '%' : '',
      },
      labelLine: { show: false },
      data,
    }],
  });
  return c;
}
