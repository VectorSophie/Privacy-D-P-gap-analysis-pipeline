import asyncio
from urllib.parse import urlparse
from typing import List, Dict
from playwright.async_api import async_playwright, Request

class TrackerDetector:
    """
    Playwright 네트워크 요청 로그를 기반으로 서드파티 트래커를 탐지하고 분류하는 모듈입니다.
    """
    
    # 추적 목적이 아닌 순수 정적 리소스 및 CDN 제외 규칙
    EXCLUDE_RESOURCE_TYPES = {'image', 'font', 'stylesheet', 'media'}
    EXCLUDE_DOMAINS = {
        'fonts.googleapis.com', 'fonts.gstatic.com', 
        'cdn.jsdelivr.net', 'cdnjs.cloudflare.com', 'unpkg.com'
    }
    
    # Known Tracker 시그니처 (도메인/키워드 매핑)
    TRACKER_DB = {
        "Analytics": [
            # Google
            "google-analytics.com", "analytics.google.com", "googletagmanager.com",
            "googletagservices.com", "ssl.google-analytics.com",
            # Naver
            "wcs.naver.com", "wcs.naver.net", "ssl.pstatic.net",
            "naver.com/v/", "logf.naver.com",
            # Kakao / Daum
            "t1.kakaocdn.net", "t1.daumcdn.net", "stat.tiara.kakao.com",
            "analytics.daumkakao.com", "analytics.kakao.com",
            # US SaaS analytics
            "mixpanel.com", "amplitude.com", "segment.com", "clarity.ms",
            "appsflyer.com", "adjust.com", "branch.io",
            "analytics.twitter.com", "stats.g.doubleclick.net",
            # Korean analytics
            "acecounter.com", "beusable.net", "channeltalk.io",
            "ablena.com", "acryl.io", "igaworks.com",
            # Additional global
            "heap.io", "kissmetrics.com", "woopra.com", "pendo.io",
            "intercom.io", "intercomcdn.com", "posthog.com",
            "rudderstack.com", "plausible.io", "matomo.org",
        ],
        "Advertising": [
            # Google Ads
            "doubleclick.net", "googlesyndication.com", "googleadservices.com",
            "adtrafficquality.google", "pagead2.googlesyndication.com",
            "ep1.adtrafficquality.google", "adservice.google.com",
            # Naver 광고
            "nam.veta.naver.com", "naver.com/ads", "spi.maps.naver.com",
            "target.naver.com", "ads.naver.com", "naver.com/ad",
            # Kakao 광고
            "adfit.kakao.com", "kakaotrack.com", "kakao.com/track",
            "pixel.kakao.com", "ka-f.kakao.com",
            # Meta
            "facebook.net", "connect.facebook.net",
            # Korean ad networks
            "criteo.com", "mobon.net", "tnk-factory.com",
            "nasmedia.co.kr", "realclick.co.kr", "nsmesns.com",
            "dmcmedia.co.kr", "mezzo.co.kr", "cauly.net",
            # Global ad networks
            "ads-twitter.com", "advertising.com", "outbrain.com",
            "taboola.com", "tradedoubler.com", "awin1.com",
            "adsrvr.org", "rubiconproject.com", "pubmatic.com",
            "openx.net", "adnxs.com", "casalemedia.com",
            "bidswitch.net", "lijit.com", "sovrn.com",
        ],
        "Session Replay": [
            "hotjar.com", "fullstory.com", "smartlook.com",
            "mouseflow.com", "contentsquare.net", "logrocket.com",
            "inspectlet.com", "lucky-orange.com", "crazyegg.com",
            "sessioncam.com", "clicktale.net", "glassbox.com",
        ],
        "Social": [
            "facebook.com/tr", "platform.twitter.com", "linkedin.com/px",
            "sc-static.net", "snap.licdn.com", "instagram.com",
            "pinterest.com/ct", "tiktok.com", "analytics.tiktok.com",
            "s.pinimg.com",
        ],
        "Fingerprinting": [
            "siftscience.com", "threatmetrix.com", "fingerprintjs.com",
            "iovation.com", "kount.com", "signifyd.com",
            "forter.com", "sardine.ai",
        ],
    }

    def __init__(self, first_party_url: str):
        self.first_party_domain = self._get_root_domain(first_party_url)
        self.network_logs: List[Dict] = []
        self.detected_trackers: List[Dict] = []

    def _get_root_domain(self, url: str) -> str:
        """URL에서 TLD 및 Root 도메인 추출 (e.g., www.example.co.kr -> example.co.kr)"""
        try:
            parsed = urlparse(url)
            parts = parsed.netloc.split('.')
            if len(parts) > 2 and parts[-2] in ['co', 'go', 'ac', 'or', 'ne']:
                return ".".join(parts[-3:])
            return ".".join(parts[-2:])
        except Exception:
            return ""

    def _is_third_party(self, request_url: str) -> bool:
        """First-party 도메인과 비교하여 Third-party 여부 판단"""
        req_domain = self._get_root_domain(request_url)
        if not req_domain or req_domain == self.first_party_domain:
            return False
        return True

    def _is_static_resource(self, request: Request) -> bool:
        """순수 CSS, 폰트, CDN 리소스 등 추적과 무관한 자원 필터링"""
        # 리소스 타입 필터 (fetch, xhr, script, document 등만 통과)
        if request.resource_type in self.EXCLUDE_RESOURCE_TYPES:
            return True
            
        req_parsed = urlparse(request.url)
        # 공용 CDN 필터
        if req_parsed.netloc in self.EXCLUDE_DOMAINS:
            return True
            
        return False

    def _classify_tracker(self, request_url: str) -> str:
        """요청 URL 패턴을 분석하여 Taxonomy 카테고리로 분류"""
        req_parsed = urlparse(request_url)
        netloc = req_parsed.netloc.lower()
        path = req_parsed.path.lower()
        full_url = netloc + path

        # DB 순회 탐색
        for category, domains in self.TRACKER_DB.items():
            for domain in domains:
                if domain in full_url:
                    return category
                    
        return "Unknown"

    def handle_request(self, request: Request):
        """Playwright request 이벤트 발생 시 로깅 및 분류"""
        # 1. 정적 리소스 필터링
        if self._is_static_resource(request):
            return
            
        # 2. Third-party 여부 검사 (자사 도메인 제외)
        if not self._is_third_party(request.url):
            return
            
        # 3. Taxonomy 분류
        domain = urlparse(request.url).netloc
        category = self._classify_tracker(request.url)
        
        tracker_info = {
            "domain": domain,
            "url": request.url,
            "category": category,
            "resource_type": request.resource_type
        }
        
        # 전체 로그 기록
        self.network_logs.append(tracker_info)
        
        # 도메인+카테고리 조합으로 중복 제거하여 최종 감지 리스트에 추가
        is_duplicate = any(t["domain"] == domain and t["category"] == category for t in self.detected_trackers)
        if not is_duplicate:
            self.detected_trackers.append(tracker_info)

    async def detect_from_url(self, url: str) -> List[Dict]:
        """주어진 URL로 이동하여 네트워크 통신을 가로채고 트래커를 식별합니다."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            
            # Request 이벤트 핸들러 부착
            page.on("request", self.handle_request)
            
            try:
                # 동적 트래커 로딩을 위해 networkidle까지 대기
                await page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                # Timeout이 발생하더라도 그 시점까지 수집된 네트워크 로그는 유효함
                pass
                
            await browser.close()
            return self.detected_trackers
