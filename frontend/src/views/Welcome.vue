<script setup lang="ts">
import type { User } from '../api'
import { btn, card, cardLifted } from '../ui'

const props = defineProps<{ user: User | null }>()

// 操作步骤（自项目首页迁移，展开为完整指引）
const steps = [
  {
    no: 1,
    title: '创建项目',
    desc: '进入「项目」页新建项目：从个人 Git 配置导入仓库，或手动填写仓库地址并配置访问凭据，也可以直接上传代码压缩包。',
    points: ['支持 GitLab 仓库列表按组织筛选导入，可多选', '私有仓库通过 Personal Access Token 访问', '一个项目可绑定多个代码仓库（含用途说明），可预先设置多个默认黑盒测试地址'],
  },
  {
    no: 2,
    title: '发起扫描',
    desc: '进入项目页点击「发起扫描」：选择分支、扫描档位（quick / standard / deep）与模型，可选填多个内网黑盒测试地址（注明各自用途）和自定义测试指令。',
    points: ['白盒源码审计为默认能力', '黑盒渗透测试可选，仅需内网测试环境', '支持按任务追加自定义测试指令'],
  },
  {
    no: 3,
    title: '查看报告',
    desc: '扫描完成后在任务详情查看报告：漏洞按严重度分级展示，附 PoC 脚本与修复建议，支持切换中文翻译。',
    points: ['严重度 / CVSS / CWE 分级明细', '一键导出中文 PDF 报告', '可下载完整产物 zip 归档'],
  },
]

const capabilities = [
  { title: '白盒代码审计', desc: '接入 Git 仓库或上传 zip 压缩包，AI 智能体对源码做静态审计，定位漏洞所在的代码位置。' },
  { title: '黑盒渗透测试（可选）', desc: '可添加多个内网测试环境地址并注明各自用途，智能体会对运行中的每个目标发起实测验证，提交前自动探测目标是否可访问。' },
  { title: '多智能体协作', desc: 'Root Agent 统一调度，按需派生 SQLi / XSS / SSRF 等专项智能体，报告页可查看各智能体的耗时与 token 消耗。' },
  { title: '中文报告输出', desc: '自动翻译漏洞标题、描述与修复建议，支持导出中文 PDF 报告与完整产物归档。' },
]

const notes = [
  '可在「个人设置」配置个人 AI 密钥；发起新扫描时可按任务自由选择模型。',
  '黑盒测试地址仅限内网测试环境，平台会对目标地址做访问控制校验。',
  'AI 辅助测试结果仅供参考，漏洞确认与修复请结合人工复核。',
  '测试范围仅限公司内部授权资产，请勿对未授权目标发起扫描。',
]

function createProject() { location.hash = '#/projects?create=1' }
</script>

<template>
  <div>
    <!-- 欢迎横幅 -->
    <section :class="cardLifted" class="relative mb-[18px] overflow-hidden p-8">
      <!-- 氛围色块（仅装饰，不承载交互） -->
      <div class="pointer-events-none absolute -top-20 -right-16 size-64 rounded-full bg-[#e55cff]/12 blur-3xl"></div>
      <div class="pointer-events-none absolute -bottom-24 right-40 size-56 rounded-full bg-[#0099ff]/12 blur-3xl"></div>

      <div class="relative">
        <p class="text-[13px] font-semibold tracking-wide text-accent">
          {{ props.user ? `欢迎回来，${props.user.display_name}` : '内部安全测试平台' }}
        </p>
        <h1 class="mt-2 max-w-2xl text-[30px] leading-tight font-bold text-text">
          欢迎使用 <span class="text-accent">Strix</span>，三步完成一次内部安全测试
        </h1>
        <p class="mt-2.5 max-w-2xl text-[13.5px] leading-relaxed text-muted">
          接入 Git 仓库或上传代码压缩包，由 AI 模型执行白盒代码审计与黑盒渗透测试，自动生成含漏洞明细、PoC 与修复建议的中文测试报告。
        </p>
        <div class="mt-4.5 flex flex-wrap items-center gap-3">
          <button :class="btn" @click="createProject">新建项目开始</button>
          <a
            href="#/stats"
            class="cursor-pointer rounded-lg bg-text px-5.5 py-2 text-sm font-semibold text-panel transition-opacity hover:opacity-90"
          >查看统计汇总</a>
          <a href="#/tasks" class="cursor-pointer text-sm font-semibold text-accent hover:underline">浏览全部任务 →</a>
        </div>
      </div>
    </section>

    <!-- 操作步骤 -->
    <section class="mb-[18px]">
      <div class="mb-3.5">
        <h2 class="text-[18px] font-bold text-text">使用流程</h2>
        <p class="mt-0.5 text-xs text-muted">从创建项目到拿到报告的三个步骤，点击步骤内的入口可直接开始。</p>
      </div>
      <div class="grid gap-3.5 lg:grid-cols-3">
        <div v-for="s in steps" :key="s.no" :class="cardLifted" class="p-5">
          <div class="flex items-center gap-2.5">
            <div class="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent/12 text-sm font-bold text-accent">
              {{ s.no }}
            </div>
            <div class="text-[15px] font-bold text-text">{{ s.title }}</div>
          </div>
          <p class="mt-2.5 text-[13px] leading-relaxed text-body">{{ s.desc }}</p>
          <ul class="mt-3 space-y-1.5">
            <li v-for="p in s.points" :key="p" class="flex items-start gap-2 text-xs leading-relaxed text-muted">
              <span class="mt-[7px] size-1.5 shrink-0 rounded-full bg-accent/60"></span>{{ p }}
            </li>
          </ul>
          <div class="mt-3.5">
            <a
              v-if="s.no === 1" href="#/projects?create=1"
              class="cursor-pointer text-xs font-semibold text-accent hover:underline"
            >去新建项目 →</a>
            <a
              v-else-if="s.no === 2" href="#/projects"
              class="cursor-pointer text-xs font-semibold text-accent hover:underline"
            >选择项目发起扫描 →</a>
            <a
              v-else href="#/tasks"
              class="cursor-pointer text-xs font-semibold text-accent hover:underline"
            >查看任务与报告 →</a>
          </div>
        </div>
      </div>
    </section>

    <!-- 平台能力 + 使用须知 -->
    <div class="grid gap-[18px] lg:grid-cols-2">
      <div :class="card" class="!mb-0">
        <h3 class="text-sm font-semibold text-muted">平台能力</h3>
        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <div v-for="c in capabilities" :key="c.title" class="rounded-xl bg-panel2/70 p-3.5">
            <div class="text-[13px] font-bold text-text">{{ c.title }}</div>
            <p class="mt-1 text-xs leading-relaxed text-muted">{{ c.desc }}</p>
          </div>
        </div>
      </div>
      <div :class="card" class="!mb-0">
        <h3 class="text-sm font-semibold text-muted">使用须知</h3>
        <ul class="mt-3 space-y-2.5">
          <li v-for="n in notes" :key="n" class="flex items-start gap-2.5 text-[13px] leading-relaxed text-body">
            <span class="mt-[7px] size-1.5 shrink-0 rounded-full bg-accent/60"></span>{{ n }}
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>
