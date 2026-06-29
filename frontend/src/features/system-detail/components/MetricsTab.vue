<script setup>
defineProps({
  metrics: { type: Object, default: null },
  metricsLoading: { type: Boolean, default: false },
})
</script>

<template>
  <div v-if="metrics">
    <a-row :gutter="16">
      <a-col :span="12">
        <a-card class="panel-card" size="small">
          <template #title>
            <span>系统资源</span>
            <a-tag :color="metrics.prometheus.available ? 'success' : 'default'"
                   style="margin-left:8px;font-size:11px;border:none">
              {{ metrics.prometheus.available ? 'Prometheus' : '未接入' }}
            </a-tag>
          </template>
          <template #extra><a-spin v-if="metricsLoading" size="small" /></template>
          <div v-if="metrics.prometheus.available">
            <div class="metric-row">
              <div class="metric-label">
                <span>内存使用</span>
                <span class="metric-val">
                  {{ (metrics.prometheus.mem_total_mb - metrics.prometheus.mem_available_mb).toFixed(0) }}
                  / {{ metrics.prometheus.mem_total_mb.toFixed(0) }} MB
                </span>
              </div>
              <a-progress :percent="metrics.prometheus.mem_used_pct"
                :stroke-color="metrics.prometheus.mem_used_pct > 85 ? '#f5222d' : metrics.prometheus.mem_used_pct > 70 ? '#faad14' : '#52c41a'"
                :format="(percent) => percent + '%'" size="small" />
            </div>
            <div class="metric-row">
              <div class="metric-label">
                <span>CPU 使用率</span>
                <span class="metric-val">{{ metrics.prometheus.cpu_usage_pct }}%</span>
              </div>
              <a-progress :percent="metrics.prometheus.cpu_usage_pct"
                :stroke-color="metrics.prometheus.cpu_usage_pct > 80 ? '#f5222d' : metrics.prometheus.cpu_usage_pct > 60 ? '#faad14' : '#52c41a'"
                :format="(percent) => percent + '%'" size="small" />
            </div>
            <div class="stat-row">
              <div class="stat-item">
                <div class="stat-label">监控目标在线</div>
                <div class="stat-num" style="color:#52c41a">
                  {{ metrics.prometheus.targets_up }}<span class="stat-denom"> / {{ metrics.prometheus.targets_total }}</span>
                </div>
              </div>
              <div class="stat-item">
                <div class="stat-label">可用内存</div>
                <div class="stat-num" style="color:var(--primary)">
                  {{ metrics.prometheus.mem_available_mb.toFixed(0) }}<span class="stat-denom"> MB</span>
                </div>
              </div>
            </div>
          </div>
          <a-empty v-else description="未注册 Prometheus 服务" :image-style="{ height: '40px' }" />
        </a-card>
      </a-col>

      <a-col :span="12">
        <a-card class="panel-card" size="small">
          <template #title>
            <span>Redis</span>
            <a-tag :color="metrics.redis.available ? 'success' : 'default'"
                   style="margin-left:8px;font-size:11px;border:none">
              {{ metrics.redis.available ? '在线' : '未接入' }}
            </a-tag>
          </template>
          <div v-if="metrics.redis.available">
            <a-row :gutter="[16, 16]">
              <a-col :span="8" v-for="stat in [
                { title:'内存占用', val: metrics.redis.used_memory_human },
                { title:'RSS 内存', val: metrics.redis.used_memory_rss_human },
                { title:'历史峰值', val: metrics.redis.used_memory_peak_human, warn: true },
                { title:'连接数', val: metrics.redis.connected_clients },
                { title:'Key 总数', val: metrics.redis.total_keys },
                { title:'OPS/s', val: metrics.redis.ops_per_sec },
                { title:'命中率', val: metrics.redis.hit_rate_pct + '%', good: metrics.redis.hit_rate_pct > 80 },
                { title:'运行天数', val: metrics.redis.uptime_days },
              ]" :key="stat.title">
                <a-statistic :title="stat.title" :value="stat.val" :value-style="{
                  fontSize: '17px',
                  fontWeight: 700,
                  color: stat.warn ? '#faad14' : stat.good === true ? '#52c41a' : undefined,
                }" />
              </a-col>
            </a-row>
          </div>
          <a-empty v-else description="未注册 Redis 服务" :image-style="{ height: '40px' }" />
        </a-card>
      </a-col>
    </a-row>

    <a-row :gutter="16" style="margin-top:16px">
      <a-col :span="12">
        <a-card v-if="metrics.kafka.available !== undefined" class="panel-card" size="small">
          <template #title>
            <span>Kafka</span>
            <a-tag :color="metrics.kafka.available ? 'success' : 'default'"
                   style="margin-left:8px;font-size:11px;border:none">
              {{ metrics.kafka.available ? '在线' : '未接入' }}
            </a-tag>
          </template>
          <div v-if="metrics.kafka.available">
            <div class="stat-row" style="margin-bottom:14px">
              <div class="stat-item">
                <div class="stat-label">Broker 数</div>
                <div class="stat-num" style="color:#52c41a">{{ metrics.kafka.brokers }}</div>
              </div>
              <div class="stat-item">
                <div class="stat-label">Topic 数</div>
                <div class="stat-num" style="color:var(--primary)">{{ metrics.kafka.topics }}</div>
              </div>
              <div class="stat-item">
                <div class="stat-label">总积压</div>
                <div class="stat-num" :style="{ color: metrics.kafka.total_lag > 0 ? '#faad14' : '#52c41a' }">
                  {{ metrics.kafka.total_lag }}
                </div>
              </div>
            </div>
            <div v-if="metrics.kafka.consumer_groups.length" class="kafka-groups">
              <div class="kafka-group-header">消费者组积压明细</div>
              <div v-for="group in metrics.kafka.consumer_groups" :key="group.group + group.topic" class="kafka-group-row">
                <span class="kafka-group-name">{{ group.group }}</span>
                <span class="kafka-group-topic">{{ group.topic }}</span>
                <span class="kafka-group-lag" :class="group.lag > 0 ? 'lag-warn' : 'lag-ok'">
                  lag {{ group.lag }}
                </span>
              </div>
            </div>
            <a-empty v-else description="暂无消费者组" :image-style="{ height: '30px' }" />
          </div>
          <a-empty v-else description="未注册 Kafka 服务或端口不可达" :image-style="{ height: '40px' }" />
        </a-card>
      </a-col>

      <a-col :span="12">
        <a-card v-if="metrics.mysql.available !== undefined" class="panel-card" size="small">
          <template #title>
            <span>MySQL</span>
            <a-tag :color="metrics.mysql.reachable ? 'success' : 'default'"
                   style="margin-left:8px;font-size:11px;border:none">
              {{ metrics.mysql.reachable ? '连接正常' : '未接入' }}
            </a-tag>
          </template>
          <div v-if="metrics.mysql.reachable">
            <div v-if="metrics.mysql.connections || metrics.mysql.uptime_hours">
              <a-row :gutter="[16, 16]">
                <a-col :span="8" v-for="stat in [
                  { title:'活跃连接', val: metrics.mysql.connections },
                  { title:'运行线程', val: metrics.mysql.threads_running },
                  { title:'QPS', val: metrics.mysql.qps },
                  { title:'运行时长', val: metrics.mysql.uptime_hours + ' h' },
                ]" :key="stat.title">
                  <a-statistic :title="stat.title" :value="stat.val"
                    :value-style="{ fontSize: '17px', fontWeight: 700 }" />
                </a-col>
              </a-row>
            </div>
            <div v-else class="mysql-no-exporter">
              <span>TCP 端口 3306 可达</span>
              <a-tag style="margin-left:8px;font-size:11px" color="default">
                部署 mysqld_exporter 可获取详细指标
              </a-tag>
            </div>
          </div>
          <a-empty v-else description="未注册 MySQL 服务或端口不可达" :image-style="{ height: '40px' }" />
        </a-card>
      </a-col>
    </a-row>

    <a-row v-if="metrics.http_services?.length" :gutter="16" style="margin-top:16px">
      <a-col :span="24">
        <a-card class="panel-card" size="small" title="HTTP 服务">
          <div class="http-svc-grid">
            <div v-for="service in metrics.http_services" :key="service.name" class="http-svc-item">
              <span :class="['http-dot', service.ok ? 'http-dot--ok' : 'http-dot--err']" />
              <span class="http-svc-name">{{ service.name }}</span>
              <span class="http-svc-code" :style="{ color: service.ok ? '#52c41a' : '#f5222d' }">
                {{ service.ok ? `HTTP ${service.status_code}` : '不可达' }}
              </span>
              <span class="http-svc-latency">{{ service.latency_ms }}ms</span>
            </div>
          </div>
        </a-card>
      </a-col>
    </a-row>
  </div>

  <a-spin v-else style="display:block;margin-top:60px;text-align:center" />
</template>

<style scoped>
.panel-card {
  border-radius: 12px;
  border: 1px solid var(--border-color);
}
.metric-row { margin-bottom: 16px; }
.metric-label {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  color: var(--text-subtle);
  margin-bottom: 5px;
}
.metric-val { font-weight: 600; color: var(--text); }
.stat-row {
  display: flex;
  gap: 32px;
  margin-top: 4px;
}
.stat-label { font-size: 12px; color: var(--text-subtle); margin-bottom: 2px; }
.stat-num { font-size: 22px; font-weight: 700; }
.stat-denom { font-size: 13px; font-weight: 400; color: var(--text-subtle); }
.kafka-groups { margin-top: 4px; }
.kafka-group-header {
  font-size: 11px;
  color: var(--text-subtle);
  text-transform: uppercase;
  letter-spacing: .06em;
  margin-bottom: 6px;
}
.kafka-group-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--border-color);
  font-size: 13px;
}
.kafka-group-row:last-child { border-bottom: none; }
.kafka-group-name { font-weight: 600; flex: 1; }
.kafka-group-topic { color: var(--text-subtle); font-size: 12px; }
.kafka-group-lag { font-size: 12px; font-weight: 600; padding: 1px 6px; border-radius: 10px; }
.lag-ok { color: #52c41a; background: rgba(82,196,26,.1); }
.lag-warn { color: #faad14; background: rgba(250,173,20,.1); }
.mysql-no-exporter {
  display: flex;
  align-items: center;
  padding: 20px 0;
  font-size: 13px;
  color: var(--text-subtle);
}
.http-svc-grid { display: flex; flex-direction: column; gap: 6px; }
.http-svc-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--body-bg);
  font-size: 13px;
}
.http-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.http-dot--ok { background: #52c41a; }
.http-dot--err { background: #f5222d; }
.http-svc-name { font-weight: 500; flex: 1; }
.http-svc-code { font-weight: 600; }
.http-svc-latency { color: var(--text-subtle); font-size: 12px; margin-left: auto; }
</style>
