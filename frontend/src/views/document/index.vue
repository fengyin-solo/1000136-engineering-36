<template>
  <section class="page" data-module="document">
    <header class="page-head">
      <div>
        <h2>体系文档管理</h2>
        <p class="page-desc">维护体系文档，围绕文档编号、文档名称、文档类型做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记体系文档</button>
        <button class="btn" type="button" @click="exportRows">导出体系文档清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无体系文档数据，可先登记体系文档</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条体系文档记录<template v-if="dataVersion"> · {{ dataVersion }}</template></span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="creating" class="modal-mask" @click.self="creating = false">
      <form class="modal-card" @submit.prevent="submitCreate">
        <h3>登记体系文档</h3>
        <label v-for="field in filterFields" :key="field" class="form-item">
          <span>{{ field }}<em>*</em></span>
          <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
        </label>
        <label v-for="field in optionalFields" :key="field" class="form-item">
          <span>{{ field }}</span>
          <input v-model="createForm[field]" :placeholder="`请输入${field}（选填）`" />
        </label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="creating = false">取消</button>
          <button class="btn primary" type="submit">确认登记</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
interface DocumentMeta {
  columns: string[]
  filter_fields: string[]
  optional_fields?: string[]
  actions: string[]
  stats: Array<{ label: string; value: number }>
  data_source: {
    schema_version: number
    source_sha256: string
    built_at: string
    used_cache: boolean
  } | null
}

const ENDPOINT = '/api/document'

// 口径全部来自后端 /meta：编号、名称、类型三字段前后端共用同一份定义
const columns = ref<string[]>([])
const filterFields = ref<string[]>([])
const optionalFields = ref<string[]>([])
const actions = ref<string[]>([])
const stats = ref<Array<{ label: string; value: number }>>([])
const dataVersion = ref('')

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = reactive<Record<string, string>>({})
const creating = ref(false)
const createForm = reactive<Record<string, string>>({})

function resetFilters() {
  for (const key of Object.keys(filters)) {
    delete filters[key]
  }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  for (const key of Object.keys(createForm)) {
    delete createForm[key]
  }
  creating.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload?.detail?.message ?? payload?.message ?? '体系文档登记失败')
    }
    creating.value = false
    await Promise.all([reload(), loadMeta()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '体系文档登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || payload?.ok === false) {
      throw new Error(payload?.detail?.message ?? payload?.message ?? '体系文档动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadMeta()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '体系文档操作失败'
  }
}

async function loadMeta() {
  const response = await request(`${ENDPOINT}/meta`)
  if (!response.ok) {
    throw new Error(`体系文档口径加载失败（${response.status}）`)
  }
  const meta = (await response.json()) as DocumentMeta
  columns.value = meta.columns
  filterFields.value = meta.filter_fields
  actions.value = meta.actions
  stats.value = meta.stats
  optionalFields.value = meta.columns.filter(
    (field) => !meta.filter_fields.includes(field) && field !== '文档状态',
  )
  if (meta.data_source) {
    dataVersion.value = `数据版本 v${meta.data_source.schema_version} · `
      + `${meta.data_source.used_cache ? '缓存' : '源文件'} · ${meta.data_source.built_at}`
  }
}

async function reload() {
  errorMessage.value = ''
  // 只提交统一口径的三个筛选字段，避免后端因未知参数拒绝
  const query = new URLSearchParams()
  for (const field of filterFields.value) {
    const value = filters[field]?.trim()
    if (value) {
      query.set(field, value)
    }
  }
  try {
    const suffix = query.toString()
    const response = await request(suffix ? `${ENDPOINT}?${suffix}` : ENDPOINT)
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.detail?.message ?? payload?.message ?? '体系文档列表读取失败')
    }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '体系文档列表读取失败'
  }
}

onMounted(async () => {
  try {
    await loadMeta()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error
      ? `${error.message}，请通过 /api/ready 查看启动诊断`
      : '体系文档初始化失败'
  }
})
</script>
