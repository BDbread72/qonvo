# Qonvo 공식 사이트

홍보 랜딩 + 다운로드 + 사용 가이드를 담은 단일 페이지. **Vite + React + framer-motion**.
라우터를 쓰지 않는 앵커 기반 단일 페이지라 어떤 경로/서브도메인에서도 정적 서빙이 가능하다
(`vite.config.js` 의 `base: './'`).

## 개발

```bash
cd site
npm install
npm run dev        # http://localhost:5173
```

## 빌드

```bash
npm run build      # → dist/  (정적 파일)
npm run preview    # 빌드 결과 로컬 확인 (http://localhost:4173)
```

## 버전 / 릴리스 관리 — GitHub Releases

출시 목록은 **GitHub Releases** 에서 가져온다 (`App.jsx` 의 `GITHUB_REPO = 'BDbread72/qonvo'`).
사이트는 빌드 때 버전을 박지 않고, 브라우저에서 GitHub API
(`/repos/BDbread72/qonvo/releases`)를 읽어 **최신 버전·다운로드·버전별 히스토리**를 자동으로 그린다.

| 요소 | 출처 |
|---|---|
| 버전 배지 / 다운로드 버튼 | 최신 릴리스의 `tag_name` + 첨부 에셋 |
| 릴리스 히스토리 | 릴리스 목록(드래프트 제외), 본문 마크다운을 헤딩/불릿으로 파싱 |
| exe / zip 다운로드 | 릴리스 **에셋 URL 직접 연결** — home 서버는 바이너리를 호스팅하지 않음 |

에셋 매핑: 파일명이 `.exe` → Windows(GUI), `.zip` → Server. 크기는 에셋 바이트에서 자동 표기.

### 새 버전 출시

1. `build.toml` 버전 올리고 평소처럼 `qonvo.exe` 빌드.
2. **GitHub 에서 릴리스 생성** — 태그 `beta-1.3.0`, 본문에 변경점(마크다운), 에셋에 `qonvo.exe`(+서버 zip) 첨부.
   ```bash
   gh release create beta-1.3.0 qonvo.exe qonvo-server-dist.zip \
     --title "beta-1.3.0" --notes-file CHANGELOG-1.3.0.md --prerelease
   ```
3. **끝.** 사이트는 손댈 게 없다 — 다음 방문 때 자동으로 새 버전이 최신으로 뜨고, 이전 버전은 히스토리에 남는다.

본문 형식은 현재 릴리스를 따른다(`## What's new` 헤더는 자동 생략, `### 그룹` 은 소제목, `- 불릿` 은 변경점).
드래프트 릴리스는 노출되지 않으니 미리 만들어두고 publish 시점에 공개하면 된다.

> 비공개 repo 로 바꾸면 API 가 401 → 사이트는 빌드시 박힌 폴백 버전만 보여준다. 공개 유지 권장.
> GitHub 미인증 API 는 IP당 60req/시간 — 사이트가 localStorage 에 10분 캐시하므로 일반 트래픽엔 충분.

## home 서버 배포 (정적 서빙)

빌드된 `dist/` 만 있으면 된다. 의존성 0 인 `serve.py` 로 띄운다:

```bash
# 로컬에서 빌드 후 dist/ 를 home 으로 전송 (tar over ssh — Windows rsync 회피)
tar -C site -czf - dist serve.py | ssh home 'mkdir -p ~/qonvo-site && tar -C ~/qonvo-site -xzf -'

# home 에서
ssh home
cd ~/qonvo-site
python3 serve.py 8080        # http://<home>:8080
```

기존 nginx(도커) 뒤에 붙이거나, qonvo.4myway.uk 의 별도 포트/경로로 노출하면 된다.
`serve.py` 는 큰 파일 Range 요청과 SPA 폴백(없는 경로 → index.html)을 지원한다.
