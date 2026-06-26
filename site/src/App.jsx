import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'

// 출시 목록은 GitHub Releases 에서 가져온다. 출시 = GitHub 에 릴리스 만들고
// exe/zip 첨부 → 사이트가 자동으로 따라옴. 서버는 손댈 필요 없음.
const GITHUB_REPO = 'BDbread72/qonvo'
const RELEASES_PAGE = `https://github.com/${GITHUB_REPO}/releases`

// Build-time fallback (root build.toml [app] version, injected by vite.config.js).
// Shown only until the GitHub API responds, or if it's unreachable / rate-limited.
const VERSION_FALLBACK = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev'
const DEFAULT_RELEASES = [
  {
    version: VERSION_FALLBACK,
    date: '',
    title: '',
    notes: [],
    html_url: `${RELEASES_PAGE}/latest`,
    downloads: [{ kind: 'app', label: 'qonvo.exe', file: `${RELEASES_PAGE}/latest`, size: '' }],
  },
]

const DL_LABEL = { app: 'Windows · GUI', server: 'Server · zip', file: '다운로드' }

function fmtSize(bytes) {
  if (!bytes) return ''
  const mb = bytes / 1048576
  return mb >= 1 ? `~${Math.round(mb)} MB` : `~${Math.max(1, Math.round(bytes / 1024))} KB`
}

function assetKind(name) {
  const n = (name || '').toLowerCase()
  if (n.endsWith('.exe')) return 'app'
  if (n.endsWith('.zip')) return 'server'
  return 'file'
}

// light inline markdown cleanup (backticks / bold / italic / links)
function cleanInline(s) {
  return s
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
}

// parse a release body into { h } (sub-heading) / { t } (note) lines
function parseNotes(body) {
  if (!body) return []
  const out = []
  for (const raw of body.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    if (/^##\s+what'?s\s+new/i.test(line)) continue // skip redundant top header
    if (/^#{1,}\s+/.test(line)) { out.push({ h: cleanInline(line.replace(/^#+\s+/, '')) }); continue }
    if (/^[-*+]\s+/.test(line)) { out.push({ t: cleanInline(line.replace(/^[-*+]\s+/, '')) }); continue }
    out.push({ t: cleanInline(line) })
  }
  return out.slice(0, 12)
}

// 버전 정렬용 키 — 'beta-1.4.10' → 1*1e6 + 4*1e3 + 10. GitHub /releases 의 응답
// 순서가 항상 최신순은 아니어서(생성 시각과 무관하게 섞임) 사이트가 직접 정렬한다.
function versionKey(v) {
  const m = String(v || '').match(/(\d+)\.(\d+)\.(\d+)/)
  return m ? Number(m[1]) * 1e6 + Number(m[2]) * 1e3 + Number(m[3]) : 0
}

function mapRelease(r) {
  return {
    version: r.tag_name || r.name,
    date: (r.published_at || '').slice(0, 10),
    title: r.name && r.name !== r.tag_name ? r.name : '',
    notes: parseNotes(r.body),
    html_url: r.html_url,
    downloads: (r.assets || []).map((a) => ({
      kind: assetKind(a.name),
      label: a.name,
      file: a.browser_download_url,
      size: fmtSize(a.size),
    })),
  }
}

const reveal = {
  hidden: { opacity: 0, y: 28 },
  show: (i = 0) => ({
    opacity: 1,
    y: 0,
    transition: { duration: 0.55, delay: i * 0.07, ease: [0.22, 1, 0.36, 1] },
  }),
}

function Reveal({ children, i = 0, className = '', style }) {
  return (
    <motion.div
      className={className}
      style={style}
      variants={reveal}
      custom={i}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, margin: '-80px' }}
    >
      {children}
    </motion.div>
  )
}

const FEATURES = [
  {
    icon: '∞',
    title: '무한 캔버스',
    body: '좌표 제한 없는 화이트보드. 줌·팬은 보이는 노드만 머티리얼라이즈하는 lazy 로딩으로, 수천 개 노드도 가볍게 다룹니다.',
  },
  {
    icon: '🤖',
    title: '멀티 프로바이더 AI',
    body: 'Gemini는 빌트인 9모델(이미지 생성 포함), Claude · GPT는 플러그인으로. 멀티 키 라운드로빈 + 스트리밍.',
    chips: ['gemini-2.0-flash', 'claude', 'gpt'],
  },
  {
    icon: '🔌',
    title: '노드 & 시그널 회로',
    body: '채팅·함수·스티키·이미지·마크다운 노드를 포트로 연결. AND/OR/NOT/XOR 로직 게이트로 실행 흐름을 회로처럼 설계합니다.',
  },
  {
    icon: '🛰️',
    title: '실시간 협업 서버',
    body: '같은 보드를 여럿이 동시에 편집. 서버가 AI 실행을 대행하고, 커서·접속자·핑을 실시간 공유합니다. UPnP 자동 포트개방.',
  },
  {
    icon: '📦',
    title: '마크 서버식 배포',
    body: 'Python만 있으면 받아서 실행. 첫 구동 시 venv·의존성을 자가설치하는 .jar 스타일 런처. 권한은 Visitor / Member / Operator.',
  },
  {
    icon: '⌨️',
    title: 'CLI 헤드리스 모드',
    body: 'GUI 없이 터미널에서 실행·REPL·배치. prompt-toolkit 기반 split 레이아웃으로 동시 작업을 관리합니다.',
  },
]

const STATS = [
  { n: '∞', l: '무한 캔버스' },
  { n: '23+', l: '노드 종류' },
  { n: '3', l: 'AI 프로바이더' },
  { n: '100%', l: '오픈 셀프호스팅' },
]

const GUIDES = {
  앱: [
    { h: '다운로드 & 실행', p: 'qonvo.exe를 받아 더블클릭하면 바로 실행됩니다. 설치 과정이 없습니다.' },
    { h: 'API 키 입력', p: '설정에서 Gemini / Anthropic / OpenAI 키를 넣으면 머신 종속 암호화로 저장됩니다.', code: '설정 → API 키 → 붙여넣기 → 저장' },
    { h: '노드 추가', p: '캔버스 우클릭으로 채팅·함수·이미지 노드를 추가하고, 출력 포트를 드래그해 다음 노드로 연결합니다.' },
    { h: '저장', p: '보드는 .qonvo 바이너리 포맷으로 원자적 저장됩니다. 첨부 이미지까지 한 파일에.' },
  ],
  노드: [
    { h: '채팅 노드', p: '프롬프트를 입력하면 스트리밍으로 응답이 채워지고, 완료 시 다음 노드로 시그널을 흘려보냅니다.' },
    { h: '함수 노드', p: '내부 그래프(start → llm_call → condition → end)를 walk하며 조건 분기를 실행합니다.' },
    { h: '로직 게이트', p: '스위치·래치 + AND/OR/NOT/XOR 게이트로 시그널 회로를 구성해 노드 실행을 제어합니다.' },
    { h: '이미지 노드', p: 'Gemini 이미지 생성 결과를 카드로 표시. 서버 모드에서는 파일로 영속화됩니다.' },
  ],
  서버: [
    { h: '받아서 압축 해제', p: 'qonvo-server-dist.zip을 풀면 런처와 소스가 들어 있습니다. Python만 있으면 됩니다.' },
    { h: '자가설치 실행', p: '런처를 실행하면 첫 구동에서 venv 생성 + 의존성 설치가 자동으로 진행됩니다.', code: './qonvo-server.sh run   # Windows: qonvo-server.bat run' },
    { h: '계정 만들기', p: '레벨 0(Visitor) / 1(Member) / 2(Operator) 중 선택해 사용자를 추가합니다.', code: 'python -m server adduser <id> <pw> 2' },
    { h: '외부 접속', p: 'UPnP가 라우터에 포트를 자동 개방합니다. config.toml의 public_host로 클라에 접속 주소를 안내하세요.' },
  ],
}

// smooth horizontal bezier between two points
function bez(x1, y1, x2, y2) {
  const dx = Math.max(40, (x2 - x1) * 0.5)
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`
}

function HeroMock() {
  // coordinate space = the .canvas-stage (900 x 360). ports are computed
  // from node geometry so the wires actually land on them.
  const prompt = { x: 18, y: 78, w: 188, h: 70 }
  const ai = { x: 300, y: 40, w: 256, h: 184 }
  const image = { x: 656, y: 26, w: 196, h: 80 }
  const text = { x: 656, y: 188, w: 196, h: 86 }

  const pOut = [prompt.x + prompt.w, prompt.y + prompt.h / 2]
  const aiIn = [ai.x, ai.y + 70]
  const aiOut1 = [ai.x + ai.w, ai.y + 56]
  const aiOut2 = [ai.x + ai.w, ai.y + 132]
  const imgIn = [image.x, image.y + image.h / 2]
  const txtIn = [text.x, text.y + text.h / 2]

  const edges = [
    { id: 'e1', d: bez(...pOut, ...aiIn), c: '#3b86ff' },
    { id: 'e2', d: bez(...aiOut1, ...imgIn), c: '#a0d911' },
    { id: 'e3', d: bez(...aiOut2, ...txtIn), c: '#ff6ec7' },
  ]
  const ports = [
    { p: pOut, c: '#3b86ff' }, { p: aiIn, c: '#3b86ff' },
    { p: aiOut1, c: '#a0d911' }, { p: aiOut2, c: '#ff6ec7' },
    { p: imgIn, c: '#a0d911' }, { p: txtIn, c: '#ff6ec7' },
  ]

  return (
    <motion.div
      className="canvas-mock"
      initial={{ opacity: 0, y: 50, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.8, delay: 0.25, ease: [0.22, 1, 0.36, 1] }}
    >
      <div className="toolbar">
        <span className="tdot" style={{ background: '#ff5f56' }} />
        <span className="tdot" style={{ background: '#ffbd2e' }} />
        <span className="tdot" style={{ background: '#27c93f' }} />
        <span style={{ marginLeft: 10, fontSize: 12, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
          board · board.qonvo
        </span>
      </div>

      <div className="canvas-stage">
        <svg className="edge-svg" viewBox="0 0 900 360" width="900" height="360">
          {edges.map((e) => (
            <g key={e.id}>
              <path id={e.id} d={e.d} stroke={e.c} strokeWidth="2.5" fill="none" opacity="0.55" />
              {/* data flowing along the wire — emphasizes the AI pipeline */}
              <circle r="3.5" fill={e.c}>
                <animateMotion dur="1.8s" repeatCount="indefinite" begin={`${edges.indexOf(e) * 0.5}s`}>
                  <mpath href={`#${e.id}`} />
                </animateMotion>
              </circle>
            </g>
          ))}
          {ports.map((pt, i) => (
            <circle key={i} cx={pt.p[0]} cy={pt.p[1]} r="4.5" fill={pt.c} stroke="#0e1014" strokeWidth="2" />
          ))}
        </svg>

        {/* prompt node */}
        <FloatNode style={{ left: prompt.x, top: prompt.y, width: prompt.w }} delay={0.5}>
          <div className="nhead" style={{ color: '#3b86ff' }}><span className="ndot" style={{ background: '#3b86ff' }} /> 프롬프트</div>
          <div className="nbody">오늘 회의 내용 요약해줘</div>
        </FloatNode>

        {/* AI model node — the centerpiece */}
        <FloatNode className="node-card ai" style={{ left: ai.x, top: ai.y, width: ai.w }} delay={0.62}>
          <div className="nhead ai-head">
            <span className="ai-spark">✦</span> AI 모델
            <span className="ai-live">생성 중</span>
          </div>
          <div className="ai-models">
            <span className="mchip active">Gemini</span>
            <span className="mchip-sep" />
            <span className="mchip">Claude</span>
            <span className="mchip">GPT</span>
          </div>
          <div className="ai-stream">
            <span>회의 핵심은 세 가지입니다</span>
            <span className="caret" />
          </div>
          <div className="ai-bars">
            <i /><i /><i /><i /><i />
          </div>
        </FloatNode>

        {/* outputs */}
        <FloatNode style={{ left: image.x, top: image.y, width: image.w }} delay={0.78}>
          <div className="nhead" style={{ color: '#a0d911' }}><span className="ndot" style={{ background: '#a0d911' }} /> 이미지 노드</div>
          <div className="nbody">🖼️ generated.png</div>
        </FloatNode>
        <FloatNode style={{ left: text.x, top: text.y, width: text.w }} delay={0.88}>
          <div className="nhead" style={{ color: '#ff6ec7' }}><span className="ndot" style={{ background: '#ff6ec7' }} /> 텍스트 응답</div>
          <div className="nbody">✓ 완료 · 시그널 emit</div>
        </FloatNode>
      </div>
    </motion.div>
  )
}

function FloatNode({ children, style, className = 'node-card', delay = 0 }) {
  return (
    <motion.div
      className={className}
      style={{ position: 'absolute', ...style }}
      initial={{ opacity: 0, scale: 0.85 }}
      animate={{ opacity: 1, scale: 1, y: [0, -6, 0] }}
      transition={{
        opacity: { delay, duration: 0.4 },
        scale: { delay, duration: 0.4 },
        y: { repeat: Infinity, duration: 3.6 + delay, ease: 'easeInOut' },
      }}
    >
      {children}
    </motion.div>
  )
}

// One release row — reused by the landing (latest only) and the full history page.
function ReleaseItem({ r, i, latest = false }) {
  return (
    <Reveal i={Math.min(i, 3)} className="rel-item">
      <div className="rel-side">
        <span className="rel-ver">{r.version}</span>
        {r.date && <span className="rel-date">{r.date}</span>}
        {latest && <span className="rel-latest">최신</span>}
      </div>
      <div className="rel-main">
        {r.title && <h3>{r.title}</h3>}
        {r.notes?.length > 0 && (
          <ul className="rel-notes">
            {r.notes.map((n, k) =>
              n.h ? <li key={k} className="rel-head">{n.h}</li> : <li key={k}>{n.t}</li>
            )}
          </ul>
        )}
        <div className="rel-dl">
          {r.downloads?.map((d) => (
            <a key={d.file} className="rel-dlbtn" href={d.file} target="_blank" rel="noreferrer">
              {DL_LABEL[d.kind] || d.kind}{d.size && <span>{d.size}</span>}
            </a>
          ))}
          {r.html_url && (
            <a className="rel-more" href={r.html_url} target="_blank" rel="noreferrer">GitHub에서 보기 ↗</a>
          )}
        </div>
      </div>
    </Reveal>
  )
}

// 출시 목록을 GitHub Releases 에서 가져오는 훅. 랜딩/히스토리 두 뷰가 공유한다.
// localStorage 에 10분 캐시(미인증 API 60req/시간 보호) + 폴백 처리.
function useReleases() {
  const [releases, setReleases] = useState(DEFAULT_RELEASES)
  useEffect(() => {
    const KEY = 'qonvo_releases_v2'   // v2: 버전 정렬 적용 — 옛 캐시(미정렬) 무효화
    try {
      const c = JSON.parse(localStorage.getItem(KEY) || 'null')
      if (c?.data?.length) setReleases(c.data)
      if (c?.ts && Date.now() - c.ts < 600000 && c?.data?.length) return // fresh cache
    } catch {}
    fetch(`https://api.github.com/repos/${GITHUB_REPO}/releases?per_page=20`, {
      headers: { Accept: 'application/vnd.github+json' },
    })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((arr) => {
        const data = arr.filter((r) => !r.draft).map(mapRelease)
          .sort((a, b) => versionKey(b.version) - versionKey(a.version))
        if (data.length) {
          setReleases(data)
          try { localStorage.setItem(KEY, JSON.stringify({ ts: Date.now(), data })) } catch {}
        }
      })
      .catch(() => {})
  }, [])
  return releases
}

// 현재 해시가 가리키는 라우트. 정적 서빙에서도 동작하도록 해시 기반.
function getRoute() {
  return (typeof location !== 'undefined' && location.hash.startsWith('#/releases')) ? 'releases' : 'home'
}

function useRoute() {
  const [route, setRoute] = useState(getRoute())
  useEffect(() => {
    const on = () => {
      setRoute(getRoute())
      // 라우트 전환 시 항상 상단에서 시작
      if (getRoute() === 'releases') window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

// 공용 상단 내비게이션. 랜딩은 앵커, 히스토리에서는 홈으로 되돌아가는 링크를 노출한다.
function Nav({ home = true }) {
  return (
    <nav className="nav">
      <div className="container nav-inner">
        <a className="brand" href={home ? '#top' : './'}>
          <img src="./icon.png" alt="Qonvo" /> Qonvo
        </a>
        <div className="nav-links">
          {home ? (
            <>
              <a href="#features">기능</a>
              <a href="#download">다운로드</a>
              <a href="#/releases">릴리스</a>
              <a href="#guide">가이드</a>
              <a href="#download" className="nav-cta">받기</a>
            </>
          ) : (
            <>
              <a href="./">홈</a>
              <a href="./#download">다운로드</a>
              <a href="./#guide">가이드</a>
              <a href="./#download" className="nav-cta">받기</a>
            </>
          )}
        </div>
      </div>
    </nav>
  )
}

function Footer({ version }) {
  return (
    <footer>
      <div className="container foot-inner">
        <a className="brand" href="./"><img src="./icon.png" alt="" style={{ width: 24, height: 24, borderRadius: 6 }} /> Qonvo</a>
        <div className="foot-links">
          <a href="./#features">기능</a>
          <a href="./#download">다운로드</a>
          <a href="#/releases">릴리스</a>
          <a href="./#guide">가이드</a>
        </div>
        <div style={{ fontSize: 13 }}>{version} · © 2026 Qonvo</div>
      </div>
    </footer>
  )
}

// 전체 버전 히스토리 — 별도 경로(#/releases). 메인에 다 쌓지 않고 여기서만 전부 그린다.
function ReleasesPage() {
  const releases = useReleases()
  const VERSION = releases[0]?.version
  return (
    <>
      <div className="bg-grid" />
      <div className="bg-glow" />
      <Nav home={false} />
      <section id="releases" style={{ paddingTop: 120 }}>
        <div className="container">
          <div className="sec-head">
            <Reveal><div className="eyebrow">Releases</div></Reveal>
            <Reveal i={1}><h2 className="sec-title">버전 히스토리</h2></Reveal>
            <Reveal i={2}><p className="sec-sub">출시한 모든 버전의 변경점과 다운로드. 최신은 <a href="./#download" style={{ color: 'var(--accent, #3b86ff)' }}>홈</a>에서 바로 받을 수 있습니다.</p></Reveal>
          </div>
          <div className="rel-list">
            {releases.map((r, i) => (
              <ReleaseItem key={r.version} r={r} i={i} latest={i === 0} />
            ))}
          </div>
        </div>
      </section>
      <Footer version={VERSION} />
    </>
  )
}

export default function App() {
  const route = useRoute()
  const [guide, setGuide] = useState('앱')
  const releases = useReleases()

  if (route === 'releases') return <ReleasesPage />

  const latest = releases[0]
  const VERSION = latest.version
  const appDl = latest.downloads?.find((d) => d.kind === 'app')
  const serverDl = latest.downloads?.find((d) => d.kind === 'server')
  const appHref = appDl?.file || `${RELEASES_PAGE}/latest`
  const serverHref = serverDl?.file || `${RELEASES_PAGE}/latest`

  return (
    <>
      <div className="bg-grid" />
      <div className="bg-glow" />

      <Nav home={true} />

      <header className="hero" id="top">
        <div className="container">
          <Reveal>
            <span className="badge"><span className="dot" /> {VERSION} · 셀프호스팅 협업 서버</span>
          </Reveal>
          <Reveal i={1}>
            <h1>
              AI 노드로 짜는<br /><span className="grad">무한 캔버스 화이트보드</span>
            </h1>
          </Reveal>
          <Reveal i={2}>
            <p className="sub">
              Gemini · Claude · GPT를 노드로 잇고, 로직 회로로 흐름을 설계하고,
              같은 보드를 여럿이 실시간으로 편집하세요. 하나의 무한 캔버스에서.
            </p>
          </Reveal>
          <Reveal i={3}>
            <div className="cta-row">
              <a className="btn btn-primary" href="#download">
                Windows 받기 <span className="sz">· {VERSION}</span>
              </a>
              <a className="btn btn-ghost" href="#guide">사용 가이드 →</a>
            </div>
          </Reveal>
          <HeroMock />
        </div>
      </header>

      <section id="features">
        <div className="container">
          <div className="sec-head">
            <Reveal><div className="eyebrow">Features</div></Reveal>
            <Reveal i={1}><h2 className="sec-title">노드 하나하나가 살아 움직입니다</h2></Reveal>
            <Reveal i={2}><p className="sec-sub">대화·이미지·로직·협업을 같은 캔버스 위에서. 필요한 것만 꺼내 연결하세요.</p></Reveal>
          </div>
          <div className="feat-grid">
            {FEATURES.map((f, i) => (
              <Reveal key={f.title} i={i % 3} className={`feat-card${i === 1 ? ' wide' : ''}`}>
                <div className="ficon">{f.icon}</div>
                <h3>{f.title}</h3>
                <p>{f.body}</p>
                {f.chips && (
                  <div className="chips">
                    {f.chips.map((c) => <span className="chip" key={c}>{c}</span>)}
                  </div>
                )}
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <section style={{ paddingTop: 0 }}>
        <div className="container">
          <Reveal>
            <div className="stats">
              {STATS.map((s) => (
                <div className="stat" key={s.l}>
                  <div className="n">{s.n}</div>
                  <div className="l">{s.l}</div>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
      </section>

      <section id="download">
        <div className="container">
          <div className="sec-head">
            <Reveal><div className="eyebrow">Download</div></Reveal>
            <Reveal i={1}><h2 className="sec-title">받아서 바로 시작</h2></Reveal>
            <Reveal i={2}><p className="sec-sub">데스크톱 앱은 설치가 필요 없고, 서버는 Python만 있으면 자가설치됩니다.</p></Reveal>
          </div>
          <div className="dl-grid">
            <Reveal className="dl-card">
              <div className="tag">// 데스크톱 앱 (GUI)</div>
              <h3>Qonvo for Windows</h3>
              <p>무한 캔버스 화이트보드 본체. 더블클릭이면 끝 — 별도 설치 과정이 없습니다.</p>
              <div className="meta"><span><b>플랫폼</b> Windows 10/11</span><span><b>크기</b> {appDl?.size || '~90 MB'}</span><span><b>버전</b> {VERSION}</span></div>
              <a className="btn btn-primary" href={appHref} download>qonvo.exe 다운로드</a>
            </Reveal>
            <Reveal i={1} className="dl-card">
              <div className="tag">// 협업 서버 (헤드리스)</div>
              <h3>Qonvo Server</h3>
              <p>마크 서버처럼 받아서 실행하는 셀프호스팅 협업 서버. PyQt6 없이 동작합니다.</p>
              <div className="meta"><span><b>요구</b> Python 3.11+</span><span><b>크기</b> {serverDl?.size || '~350 KB'}</span><span><b>접속</b> UPnP 자동</span></div>
              <a className="btn btn-ghost" href={serverHref} download>qonvo-server-dist.zip</a>
            </Reveal>
          </div>
        </div>
      </section>

      <section id="guide">
        <div className="container">
          <div className="sec-head">
            <Reveal><div className="eyebrow">Guide</div></Reveal>
            <Reveal i={1}><h2 className="sec-title">사용 가이드</h2></Reveal>
            <Reveal i={2}><p className="sec-sub">앱 시작부터 노드 활용, 서버 호스팅까지 단계별로.</p></Reveal>
          </div>
          <Reveal>
            <div className="guide-tabs">
              {Object.keys(GUIDES).map((g) => (
                <button key={g} className={`guide-tab${guide === g ? ' active' : ''}`} onClick={() => setGuide(g)}>
                  {g === '앱' ? '앱 시작하기' : g === '노드' ? '노드 활용' : '서버 호스팅'}
                </button>
              ))}
            </div>
          </Reveal>
          <div className="steps">
            {GUIDES[guide].map((s, i) => (
              <Reveal key={s.h} i={i} className="step">
                <div className="num">{i + 1}</div>
                <div>
                  <h4>{s.h}</h4>
                  <p>{s.p}</p>
                  {s.code && <div className="code">{s.code}</div>}
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <section id="releases">
        <div className="container">
          <div className="sec-head">
            <Reveal><div className="eyebrow">Releases</div></Reveal>
            <Reveal i={1}><h2 className="sec-title">최신 버전</h2></Reveal>
            <Reveal i={2}><p className="sec-sub">가장 최근 출시만 여기 둡니다. 지난 버전들은 전체 히스토리에서.</p></Reveal>
          </div>
          <div className="rel-list">
            {latest && <ReleaseItem r={latest} i={0} latest />}
          </div>
          <Reveal i={1} style={{ textAlign: 'center', marginTop: 28 }}>
            <a className="btn btn-ghost" href="#/releases">전체 버전 히스토리 →</a>
          </Reveal>
        </div>
      </section>

      <section>
        <div className="container">
          <Reveal>
            <div className="cta-band">
              <h2>지금 캔버스를 펼쳐보세요</h2>
              <p>설치 없이 더블클릭, 서버는 Python만 있으면 자가설치.</p>
              <div className="cta-row">
                <a className="btn btn-primary" href={appHref} download>Windows 받기 <span className="sz">· {VERSION}</span></a>
                <a className="btn btn-ghost" href="#guide">가이드 보기 →</a>
              </div>
            </div>
          </Reveal>
        </div>
      </section>

      <Footer version={VERSION} />
    </>
  )
}
