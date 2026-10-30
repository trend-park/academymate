# 학원메이트 관리형 배포 연결안

저장소: https://github.com/trend-park/academymate

기존 확인 커밋: `0908fbfad10af7d427439ff0ca277777ba273fce`

## 현재 확인된 구조

- `CNAME`: `hakwonmate.co.kr`
- 실제 홈페이지 HTTP 응답: `Server: GitHub.com`
- 기존 도구함: `/lab-t5k9w/`
- 기존 홈페이지와 가비아의 루트 도메인·www·메일 설정은 유지합니다.

## 준비한 연결

- 개인 순위 서비스 주소: `https://rank.hakwonmate.co.kr/`
- 공개 결과 주소: `https://rank.hakwonmate.co.kr/s/<임의 토큰>`
- 도구함 링크 추가 변경안: `academymate-rank-link.patch`
- 별도 서비스 소스: 이 폴더의 Python·HTML·배포 파일

이 주소들은 계획한 주소이며 아직 DNS나 실제 배포가 완료된 것이 아닙니다.

## GitHub에 올릴 파일

독립 저장소로 배포할 때는 이 폴더를 저장소 루트로 올리고 `render.yaml`을 선택합니다. 기존 `academymate` 저장소를 사용할 때는 `rank-service/` 폴더에 이 소스를 넣고, Render 서비스의 `rootDir`을 `rank-service`로 지정해야 합니다. 두 방식 중 하나만 사용합니다.

`data/`, `.deps/`, `.env`, `.venv/`, `__pycache__/`는 제외합니다. DB·실제 회원 정보·연결 문자열·비밀번호를 GitHub에 올리지 않습니다. 기존 홈페이지는 별도로 유지하며 배포가 확인된 후 도구함 링크만 추가합니다.

## Render에서 실행

1. 본인 GitHub와 Render 계정을 연결합니다.
2. 준비한 소스와 Blueprint를 연결하고 웹 서비스·DB의 리전·상품·실제 비용을 확인합니다. 현재 Blueprint는 Singapore, 웹 2GB(`1c-2g`), PostgreSQL 256MB(`0.1c-256mb`)의 시험 운영 구성이며 사용량에 맞춰 검토해야 합니다.
3. 비용 승인 후 리소스를 생성합니다. `DATABASE_URL`은 DB 연결 설정에서 자동 주입되도록 선언되어 있습니다.
4. Render의 임시 HTTPS 주소로 회원가입·복구 코드 보관·키워드 설정·순위 체크·결과 공유를 검증합니다. 서비스 생성·배포 성공과 실제 PostgreSQL 검증이 완료되기 전에는 운영 준비가 끝난 것으로 보지 않습니다.
5. Render의 Custom Domains에 `rank.hakwonmate.co.kr`을 추가합니다.
6. 가비아 DNS 관리에서 호스트 `rank`에 Render가 반환한 정확한 대상으로 CNAME을 설정합니다. 같은 이름의 기존 레코드가 있으면 먼저 충돌 여부를 확인합니다. 루트·www·MX·네임서버는 수정하지 않습니다.
7. 도메인 검증·HTTPS 인증서와 실제 접속을 확인합니다.
8. 검증 후 기존 도구함에 새 서비스 링크를 연결합니다.

특정 플랫폼 계정의 비밀번호·API 키를 채팅이나 GitHub에 적지 않고 연결 절차를 사용합니다. 유료 리소스를 만들거나 DNS·원격 저장소를 변경하는 작업은 아직 수행하지 않았습니다.

## 운영에서 남는 일

관리형 플랫폼이 배포·서버 실행을 맡아도 네이버 수집 구조 변경, 오류 확인, 백업·복구 점검과 호스팅 비용 확인은 필요합니다. 선생님이 회원별 키워드나 순위를 일일이 관리하는 기능은 없습니다.
