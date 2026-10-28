# Yamtrack: add `GET /api/v1/providers/` (every watch provider of a region, with icons)

Target repo and branch: `Breezyslasher/Yamtrack`, `feat/justwatch-links`.

## Why

The Yamtrack Home Assistant integration needs the full list of streaming services available in a region, each with its icon. It shows them as an icon grid so users can pick "My services". Today the API only returns providers per title (`/media/{type}/{source}/{id}/providers/`), so the integration has to guess the list from a few popular titles.

## Endpoint contract

`GET /api/v1/providers/?region=US`

- Auth: same as the other endpoints (`X-API-Key` header or `Authorization: Bearer`).
- `region`: ISO 3166-1 code, case-insensitive. Falls back to the user's `watch_provider_region`. If neither is set, return 400. Reuse `parse_watch_provider_region`.
- 200 response:

```json
{
  "region": "US",
  "results": [
    {
      "provider_id": 8,
      "provider_name": "Netflix",
      "image": "https://image.tmdb.org/t/p/w500/pbpMk2JmcoNnQwx5JGpXngfoWtp.jpg",
      "display_priority": 0
    }
  ]
}
```

- `results` is the complete list, not paginated, sorted by `display_priority` ascending.
- `provider_id` and `provider_name` must match the values the per-title providers endpoint returns, because the integration matches on `provider_name`.
- On an upstream failure, return 500 with `detail` and `errors`, like the other views.

## How it should work

1. **TMDB (always).** Call `GET {tmdb}/watch/providers/movie` and `GET {tmdb}/watch/providers/tv` with `watch_region=<region>`.
   - Merge the two lists by `provider_id`.
   - Use `display_priorities[region]` when present, otherwise `display_priority`.
   - Build `image` with `tmdb.get_image_url(logo_path)`.
   - Cache per region with the default TMDB cache timeout.
2. **JustWatch (only when `JUSTWATCH_ENABLED`).** POST this GraphQL query to `https://apis.justwatch.com/graphql`, using the same headers as the existing offer lookup:

   ```graphql
   query GetPackages($country: Country!, $platform: Platform!) {
     packages(country: $country, platform: $platform, includeAddons: true) {
       packageId
       clearName
       icon(profile: S100, format: PNG)
     }
   }
   ```

   Send it with variables `{"country": region, "platform": "WEB"}`.
   - JustWatch `packageId` equals the TMDB `provider_id`, which the existing `apply_offers` code already relies on.
   - Add the packages TMDB doesn't list, with `display_priority = 1000 + order` and `image = "https://images.justwatch.com" + icon`.
   - Skip packages with no `packageId` or no `clearName`.
   - Cache for `CACHE_TIMEOUT` (6 hours).
   - If JustWatch fails, log it and return the TMDB list.
3. **Wiring.**
   - URL name `api_providers`, regex `^providers/?$`, placed before `search/` in `src/api/urls.py`.
   - Serializers `WatchProviderListItemSerializer` and `WatchProviderListResponseSerializer`.
   - `extend_schema` with `operation_id="providers_get"`.
   - Add `EndpointCase("get", "api_providers")` to `src/api/tests/endpoints.py`, so the auth tests cover it.

**Not verified:** the JustWatch `packages` query has not been run against the live JustWatch API. I couldn't reach JustWatch from my sandbox. Please test it with `JUSTWATCH_ENABLED=True` against a real region. If the field or arguments differ, only `justwatch.get_packages` needs to change; the TMDB path works without it.

## Tests (`src/api/tests/test_providers.py`)

- The movie and TV lists merge and sort by the region's priority, and fall back to the global priority.
- Image URLs are built correctly, and the response is cached per region.
- With JustWatch enabled, providers only JustWatch has are appended with their icon.
- A JustWatch failure still returns the TMDB list and logs an error.
- No region and no user preference returns 400.

On my side, these tests passed together with `test_authentication.py` and `test_justwatch.py`: `17 passed, 160 subtests passed`.

## Reference implementation

This is the patch I wrote and tested locally against `feat/justwatch-links` at `caed951`. Nothing was pushed. Apply it with `git apply`, or use it as a guide:

```diff
diff --git a/src/api/helpers.py b/src/api/helpers.py
index 711ab7e..5c43521 100644
--- a/src/api/helpers.py
+++ b/src/api/helpers.py
@@ -484,6 +484,37 @@ def add_justwatch_links(providers, media_type, media_id, title, quality):
     return providers
 
 
+def list_watch_providers(region):
+    """Return every watch provider of a region from TMDB and JustWatch.
+
+    TMDB providers keep their order. When JustWatch is enabled, its providers
+    that TMDB doesn't list follow them; JustWatch failures are ignored.
+    """
+    providers = [dict(provider) for provider in tmdb.watch_providers(region)]
+    if not justwatch.is_enabled():
+        return providers
+
+    try:
+        packages = justwatch.get_packages(region)
+    except Exception:
+        logger.exception("Failed to fetch JustWatch providers for %s", region)
+        return providers
+
+    known = {provider["provider_id"] for provider in providers}
+    for order, (provider_id, package) in enumerate(packages.items()):
+        if provider_id in known:
+            continue
+        providers.append(
+            {
+                "provider_id": provider_id,
+                "provider_name": package["provider_name"],
+                "image": package["image"],
+                "display_priority": 1000 + order,
+            },
+        )
+    return providers
+
+
 def check_supported_source(supported_sources, media_type, source, feature):
     """Return an error response if the media type or source is not supported."""
     if media_type not in supported_sources:
diff --git a/src/api/serializers.py b/src/api/serializers.py
index 8c145d6..6c85a5d 100644
--- a/src/api/serializers.py
+++ b/src/api/serializers.py
@@ -1699,6 +1699,22 @@ class WatchProvidersResponseSerializer(serializers.Serializer):
     paid = WatchProviderSerializer(many=True)
 
 
+class WatchProviderListItemSerializer(serializers.Serializer):
+    """Serializer for a provider of the watch provider list."""
+
+    provider_id = serializers.IntegerField()
+    provider_name = serializers.CharField()
+    image = serializers.URLField(allow_null=True)
+    display_priority = serializers.IntegerField(allow_null=True)
+
+
+class WatchProviderListResponseSerializer(serializers.Serializer):
+    """Serializer for the watch provider list endpoint."""
+
+    region = serializers.CharField()
+    results = WatchProviderListItemSerializer(many=True)
+
+
 class TimelineItemSerializer(serializers.ModelSerializer):
     """Compact serializer used for timeline entries to reduce payload size."""
 
diff --git a/src/api/tests/endpoints.py b/src/api/tests/endpoints.py
index 54722d6..8b51873 100644
--- a/src/api/tests/endpoints.py
+++ b/src/api/tests/endpoints.py
@@ -296,6 +296,7 @@ def get_endpoint_cases() -> list[EndpointCase]:
         EndpointCase("get", "api_person_detail", args=("tmdb", 1)),
         EndpointCase("get", "api_person_images", args=("tmdb", 1)),
         EndpointCase("get", "api_person_known_for", args=("tmdb", 1)),
+        EndpointCase("get", "api_providers"),
         EndpointCase("get", "api_search_provider", args=("movie",)),
         EndpointCase("get", "api_statistics"),
     ]
diff --git a/src/api/tests/test_providers.py b/src/api/tests/test_providers.py
new file mode 100644
index 0000000..77383dd
--- /dev/null
+++ b/src/api/tests/test_providers.py
@@ -0,0 +1,151 @@
+from unittest.mock import patch
+
+from django.core.cache import cache
+from django.test import override_settings
+
+from .base import YamtrackApiTestCase
+
+TMDB_MOVIE_PROVIDERS = {
+    "results": [
+        {
+            "provider_id": 8,
+            "provider_name": "Netflix",
+            "logo_path": "/netflix.jpg",
+            "display_priority": 5,
+            "display_priorities": {"US": 0, "GB": 1},
+        },
+        {
+            "provider_id": 337,
+            "provider_name": "Disney Plus",
+            "logo_path": "/disney.jpg",
+            "display_priority": 2,
+            "display_priorities": {"US": 1},
+        },
+    ],
+}
+
+TMDB_TV_PROVIDERS = {
+    "results": [
+        {
+            "provider_id": 8,
+            "provider_name": "Netflix",
+            "logo_path": "/netflix.jpg",
+            "display_priorities": {"US": 0},
+        },
+        {
+            "provider_id": 15,
+            "provider_name": "Hulu",
+            "logo_path": None,
+            "display_priority": 3,
+            "display_priorities": {},
+        },
+    ],
+}
+
+JUSTWATCH_PACKAGES = {
+    "data": {
+        "packages": [
+            {"packageId": 8, "clearName": "Netflix", "icon": "/icon/1/s100/nfx.png"},
+            {
+                "packageId": 2766,
+                "clearName": "Sling TV Freestream",
+                "icon": "/icon/321/s100/slingfreestream.png",
+            },
+            {"packageId": None, "clearName": "Broken", "icon": None},
+        ],
+    },
+}
+
+
+def tmdb_response(_provider, _method, url, **_kwargs):
+    """Return the TMDB provider list of the requested media type."""
+    if url.endswith("/watch/providers/movie"):
+        return TMDB_MOVIE_PROVIDERS
+    if url.endswith("/watch/providers/tv"):
+        return TMDB_TV_PROVIDERS
+    return JUSTWATCH_PACKAGES
+
+
+@patch("app.providers.services.api_request", side_effect=tmdb_response)
+class WatchProviderListTests(YamtrackApiTestCase):
+    """Validate the watch provider list endpoint."""
+
+    def setUp(self):
+        """Clear cached provider lists between tests."""
+        super().setUp()
+        cache.clear()
+
+    def get_providers(self, region="US"):
+        """Call the provider list endpoint."""
+        return self.call_api(
+            "get",
+            "api_providers",
+            params={"region": region},
+            headers=self.auth_headers,
+        )
+
+    @override_settings(JUSTWATCH_ENABLED=False)
+    def test_lists_tmdb_providers_by_region_priority(self, mock_request):
+        """Movie and tv providers are merged and sorted for the region."""
+        response = self.get_providers("us")
+
+        self.assertEqual(response.status_code, 200)
+        payload = response.json()
+        self.assertEqual(payload["region"], "US")
+        self.assertEqual(
+            [p["provider_name"] for p in payload["results"]],
+            ["Netflix", "Disney Plus", "Hulu"],
+        )
+        netflix = payload["results"][0]
+        self.assertEqual(netflix["provider_id"], 8)
+        self.assertEqual(netflix["display_priority"], 0)
+        self.assertEqual(netflix["image"], "https://image.tmdb.org/t/p/w500/netflix.jpg")
+        # Without a priority for the region, the global one is used
+        self.assertEqual(payload["results"][2]["display_priority"], 3)
+
+        params = mock_request.call_args_list[0].kwargs["params"]
+        self.assertEqual(params["watch_region"], "US")
+        self.assertEqual(mock_request.call_count, 2)
+
+        # Cached per region
+        self.get_providers()
+        self.assertEqual(mock_request.call_count, 2)
+
+    @override_settings(JUSTWATCH_ENABLED=True)
+    def test_adds_justwatch_only_providers(self, _mock_request):
+        """JustWatch providers TMDB doesn't list are added after TMDB's."""
+        payload = self.get_providers().json()
+
+        names = [p["provider_name"] for p in payload["results"]]
+        self.assertEqual(
+            names,
+            ["Netflix", "Disney Plus", "Hulu", "Sling TV Freestream"],
+        )
+        sling = payload["results"][-1]
+        self.assertEqual(sling["provider_id"], 2766)
+        self.assertEqual(
+            sling["image"],
+            "https://images.justwatch.com/icon/321/s100/slingfreestream.png",
+        )
+
+    @override_settings(JUSTWATCH_ENABLED=True)
+    def test_justwatch_failure_keeps_tmdb_providers(self, mock_request):
+        """A JustWatch failure falls back to the TMDB providers."""
+
+        def fail_justwatch(provider, method, url, **kwargs):
+            if provider == "justwatch":
+                msg = "JustWatch is down"
+                raise RuntimeError(msg)
+            return tmdb_response(provider, method, url, **kwargs)
+
+        mock_request.side_effect = fail_justwatch
+        with self.assertLogs("api.helpers", level="ERROR"):
+            response = self.get_providers()
+
+        self.assertEqual(response.status_code, 200)
+        self.assertEqual(len(response.json()["results"]), 3)
+
+    def test_requires_region(self, _mock_request):
+        """Without a region or a user preference the request is rejected."""
+        response = self.call_api("get", "api_providers", headers=self.auth_headers)
+        self.assertEqual(response.status_code, 400)
diff --git a/src/api/urls.py b/src/api/urls.py
index 0138d61..fe32bb7 100644
--- a/src/api/urls.py
+++ b/src/api/urls.py
@@ -238,6 +238,11 @@ urlpatterns = [
         views.PersonKnownForView.as_view(),
         name="api_person_known_for",
     ),
+    re_path(
+        r"^providers/?$",
+        views.WatchProviderListView.as_view(),
+        name="api_providers",
+    ),
     re_path(
         r"^search/(?P<media_type>[^/]+)/?$",
         views.SearchProviderView.as_view(),
diff --git a/src/api/views.py b/src/api/views.py
index f479967..f257ce0 100644
--- a/src/api/views.py
+++ b/src/api/views.py
@@ -72,6 +72,7 @@ from .helpers import (
     get_sorts,
     get_timeline,
     group_watch_providers,
+    list_watch_providers,
     paginate_data,
     parse_excluded_items,
     parse_limit_offset,
@@ -160,6 +161,7 @@ from .serializers import (
     UpdateMovieSerializer,
     UpdateSeasonSerializer,
     UpdateTVSerializer,
+    WatchProviderListResponseSerializer,
     WatchProvidersResponseSerializer,
 )
 
@@ -8869,6 +8871,66 @@ class PersonKnownForView(drf_views.APIView):
         return Response(PaginatedKnownForSerializer(paginated).data, status=HTTP.OK)
 
 
+# /api/v1/providers/
+class WatchProviderListView(drf_views.APIView):
+    """Watch provider list view."""
+
+    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
+    permission_classes = [permissions.IsAuthenticated]
+    serializer_class = WatchProviderListResponseSerializer
+
+    @extend_schema(
+        operation_id="providers_get",
+        summary="List every watch provider of a region",
+        parameters=[WatchProviderRegionParam],
+        responses={
+            200: OpenApiResponse(
+                WatchProviderListResponseSerializer,
+                description="Successful response",
+                examples=[
+                    OpenApiExample(
+                        "List watch providers example",
+                        description="List watch providers example",
+                        summary="List watch providers example",
+                        value={
+                            "region": "US",
+                            "results": [
+                                {
+                                    "provider_id": 8,
+                                    "provider_name": "Netflix",
+                                    "image": "https://image.tmdb.org/t/p/w500/pbpMk2JmcoNnQwx5JGpXngfoWtp.jpg",
+                                    "display_priority": 0,
+                                },
+                            ],
+                        },
+                    )
+                ],
+            ),
+            400: BadRequestResponse,
+            403: ForbiddenResponse,
+            500: InternalServerErrorResponse,
+        },
+    )
+    def get(self, request):
+        """List the watch providers of a region from TMDB and JustWatch."""
+        region, err = parse_watch_provider_region(request)
+        if err:
+            return err
+
+        try:
+            providers = list_watch_providers(region)
+        except Exception as e:  # noqa: BLE001
+            return Response(
+                {
+                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
+                    "errors": str(e),
+                },
+                status=HTTP.INTERNAL_SERVER_ERROR,
+            )
+
+        return Response({"region": region, "results": providers}, status=HTTP.OK)
+
+
 # /api/v1/search/[media_type]/
 class SearchProviderView(drf_views.APIView):
     """Search view."""
diff --git a/src/app/providers/justwatch.py b/src/app/providers/justwatch.py
index 2763a3f..d505f8f 100644
--- a/src/app/providers/justwatch.py
+++ b/src/app/providers/justwatch.py
@@ -75,6 +75,16 @@ query GetSearchTitles(
 }
 """
 
+packages_query = """
+query GetPackages($country: Country!, $platform: Platform!) {
+  packages(country: $country, platform: $platform, includeAddons: true) {
+    packageId
+    clearName
+    icon(profile: S100, format: PNG)
+  }
+}
+"""
+
 OBJECT_TYPES = {
     MediaTypes.MOVIE.value: "MOVIE",
     MediaTypes.TV.value: "SHOW",
@@ -132,6 +142,42 @@ def get_offers(media_type, tmdb_id, title, region):
     return data
 
 
+def get_packages(region):
+    """Return every JustWatch provider (package) of a region, keyed by provider ID.
+
+    JustWatch package IDs are the TMDB provider IDs. Each provider has its
+    `provider_name` and `image`.
+    """
+    cache_key = f"{PROVIDER}_packages_{region}"
+    data = cache.get(cache_key)
+
+    if data is None:
+        response = services.api_request(
+            PROVIDER,
+            "POST",
+            base_url,
+            headers=headers,
+            params={
+                "query": packages_query,
+                "variables": {"country": region, "platform": "WEB"},
+            },
+        )
+        packages = (response.get("data") or {}).get("packages") or []
+        data = {
+            package["packageId"]: {
+                "provider_name": package.get("clearName"),
+                "image": f"{images_url}{package['icon']}"
+                if package.get("icon")
+                else None,
+            }
+            for package in packages
+            if package.get("packageId") is not None and package.get("clearName")
+        }
+        cache.set(cache_key, data, CACHE_TIMEOUT)
+
+    return data
+
+
 def find_title(response, tmdb_id):
     """Return the search result matching the TMDB ID, None if there is none."""
     edges = ((response.get("data") or {}).get("popularTitles") or {}).get("edges") or []
diff --git a/src/app/providers/tmdb.py b/src/app/providers/tmdb.py
index 09d3485..d7e6fe2 100644
--- a/src/app/providers/tmdb.py
+++ b/src/app/providers/tmdb.py
@@ -1223,6 +1223,54 @@ def watch_provider_regions():
     return data
 
 
+def watch_providers(region):
+    """Return every watch provider TMDB lists for a region, movies and tv merged.
+
+    Each provider has its `provider_id`, `provider_name`, `image` and the
+    `display_priority` for the region, sorted by that priority.
+    """
+    cache_key = f"{Sources.TMDB.value}_watch_providers_{region}"
+    data = cache.get(cache_key)
+
+    if data is None:
+        providers = {}
+        for media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value):
+            try:
+                response = services.api_request(
+                    Sources.TMDB.value,
+                    "GET",
+                    f"{base_url}/watch/providers/{media_type}",
+                    params={**base_params, "watch_region": region},
+                )
+            except requests.exceptions.HTTPError as error:
+                handle_error(error)
+
+            for provider in response.get("results", []):
+                provider_id = provider.get("provider_id")
+                if provider_id is None or provider_id in providers:
+                    continue
+                priorities = provider.get("display_priorities") or {}
+                providers[provider_id] = {
+                    "provider_id": provider_id,
+                    "provider_name": provider.get("provider_name"),
+                    "image": get_image_url(provider.get("logo_path")),
+                    "display_priority": priorities.get(
+                        region,
+                        provider.get("display_priority"),
+                    ),
+                }
+
+        data = sorted(
+            providers.values(),
+            key=lambda e: (
+                e["display_priority"] if e["display_priority"] is not None else 999
+            ),
+        )
+        cache.set(cache_key, data)
+
+    return data
+
+
 def get_changed_ids(media_type):
     """Return changed TMDB ids for the given media type over the last days."""
     url = f"{base_url}/{media_type}/changes"
```
