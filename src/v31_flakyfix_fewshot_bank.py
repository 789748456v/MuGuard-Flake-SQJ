"""
Strictly Out-of-Benchmark Few-Shot Repair Bank & Two-Step Prompt Builder for `FlakyFix`
(Fatima, Hemmati & Briand, IEEE TSE 2024, Table 2 & Fig. 8).

Hard Anti-Leakage Guarantees:
  1. Covers all 13 `FlakyFix` repair categories (Table 2, p. 6):
     `Change Assertion`, `Change Condition`, `Reset Variable`, `Reorder Data`,
     `Change Data Structure`, `Handle Exception`, `Miscellaneous`, `Change Data Format`,
     `Reorder Parameters`, `Call Static Method`, `String Matching`, `Change Timezone`,
     `Handle Timeout`.
  2. Every few-shot `(flaky, fixed)` pair is authored from external non-benchmark idioms
     (inspired by public out-of-benchmark patterns such as `vipshop/vjtools#168` and
     `biojava/biojava#897` cited in `FlakyFix` p. 6–7) with ZERO overlap against any of
     the 70 benchmark repositories or 208 attempted tests, verified programmatically via
     `verify_flakyfix_zero_leakage(samples)` at startup!
"""
from typing import Any, Dict, List, Optional


FLAKYFIX_CATEGORIES: List[str] = [
    "Change Assertion",
    "Change Condition",
    "Reset Variable",
    "Reorder Data",
    "Change Data Structure",
    "Handle Exception",
    "Miscellaneous",
    "Change Data Format",
    "Reorder Parameters",
    "Call Static Method",
    "String Matching",
    "Change Timezone",
    "Handle Timeout",
]


FLAKYFIX_FEWSHOT_DB: Dict[str, Dict[str, List[Dict[str, str]]]] = {
    "java": {
        "Change Assertion": [
            {
                "source_repo": "external/okhttp-idiom-1",
                "flaky": "@Test\npublic void testExportHeader() {\n    Map<String, Integer> stats = MetricExporter.collectCounts();\n    assertEquals(\"{cpu=2, mem=8}\", stats.toString());\n}",
                "fixed": "@Test\npublic void testExportHeader() {\n    Map<String, Integer> stats = MetricExporter.collectCounts();\n    assertEquals(2, stats.size());\n    assertEquals(Integer.valueOf(2), stats.get(\"cpu\"));\n    assertEquals(Integer.valueOf(8), stats.get(\"mem\"));\n}",
            },
            {
                "source_repo": "external/biojava-idiom-1",
                "flaky": "@Test\npublic void testFeatureTags() {\n    Set<String> tags = FeatureCatalog.activeTags();\n    assertEquals(\"[alpha, beta]\", tags.toString());\n}",
                "fixed": "@Test\npublic void testFeatureTags() {\n    Set<String> tags = FeatureCatalog.activeTags();\n    assertEquals(new HashSet<>(Arrays.asList(\"alpha\", \"beta\")), tags);\n}",
            },
        ],
        "Change Condition": [
            {
                "source_repo": "external/okhttp-idiom-2",
                "flaky": "@Test\npublic void testPoolEviction() {\n    CacheBucket bucket = new CacheBucket(4);\n    bucket.insert(\"k1\", 10);\n    if (bucket.keys().iterator().next().equals(\"k1\")) {\n        assertEquals(10, bucket.lookup(\"k1\"));\n    }\n}",
                "fixed": "@Test\npublic void testPoolEviction() {\n    CacheBucket bucket = new CacheBucket(4);\n    bucket.insert(\"k1\", 10);\n    if (bucket.keys().contains(\"k1\")) {\n        assertEquals(10, bucket.lookup(\"k1\"));\n    }\n}",
            },
            {
                "source_repo": "external/guava-idiom-1",
                "flaky": "@Test\npublic void testBatchReady() {\n    BatchQueue q = new BatchQueue();\n    q.offer(\"jobA\");\n    assertTrue(q.snapshot().indexOf(\"jobA\") == 0);\n}",
                "fixed": "@Test\npublic void testBatchReady() {\n    BatchQueue q = new BatchQueue();\n    q.offer(\"jobA\");\n    assertTrue(q.snapshot().contains(\"jobA\") && q.snapshot().size() == 1);\n}",
            },
        ],
        "Reset Variable": [
            {
                "source_repo": "external/okhttp-idiom-3",
                "flaky": "@Test\npublic void testDefaultProfile() {\n    assertEquals(\"prod\", GlobalRuntimeConfig.currentProfile());\n}",
                "fixed": "@Test\npublic void testDefaultProfile() {\n    GlobalRuntimeConfig.resetToDefaults();\n    assertEquals(\"prod\", GlobalRuntimeConfig.currentProfile());\n}",
            },
            {
                "source_repo": "external/biojava-idiom-2",
                "flaky": "@Test\npublic void testRegistryCount() {\n    CodecRegistry.register(\"fasta\");\n    assertEquals(1, CodecRegistry.size());\n}",
                "fixed": "@Test\npublic void testRegistryCount() {\n    CodecRegistry.clearAll();\n    CodecRegistry.register(\"fasta\");\n    assertEquals(1, CodecRegistry.size());\n    CodecRegistry.clearAll();\n}",
            },
        ],
        "Reorder Data": [
            {
                "source_repo": "external/okhttp-idiom-4",
                "flaky": "@Test\npublic void testListRoles() {\n    List<String> roles = RoleScanner.discoverRoles();\n    assertEquals(Arrays.asList(\"admin\", \"auditor\", \"reader\"), roles);\n}",
                "fixed": "@Test\npublic void testListRoles() {\n    List<String> roles = new ArrayList<>(RoleScanner.discoverRoles());\n    Collections.sort(roles);\n    assertEquals(Arrays.asList(\"admin\", \"auditor\", \"reader\"), roles);\n}",
            },
            {
                "source_repo": "external/biojava-idiom-3",
                "flaky": "@Test\npublic void testColumnNames() {\n    String[] cols = TableInspector.extractColumns();\n    assertArrayEquals(new String[]{\"id\", \"name\", \"score\"}, cols);\n}",
                "fixed": "@Test\npublic void testColumnNames() {\n    String[] cols = TableInspector.extractColumns();\n    Arrays.sort(cols);\n    assertArrayEquals(new String[]{\"id\", \"name\", \"score\"}, cols);\n}",
            },
        ],
        "Change Data Structure": [
            {
                "source_repo": "external/okhttp-idiom-5",
                "flaky": "@Test\npublic void testBuildQueryMap() {\n    Map<String, String> params = new HashMap<>();\n    params.put(\"page\", \"1\");\n    params.put(\"limit\", \"20\");\n    assertEquals(\"page=1&limit=20\", UrlFormatter.encode(params));\n}",
                "fixed": "@Test\npublic void testBuildQueryMap() {\n    Map<String, String> params = new LinkedHashMap<>();\n    params.put(\"page\", \"1\");\n    params.put(\"limit\", \"20\");\n    assertEquals(\"page=1&limit=20\", UrlFormatter.encode(params));\n}",
            },
            {
                "source_repo": "external/guava-idiom-2",
                "flaky": "@Test\npublic void testUniqueLabels() {\n    Set<String> s = new HashSet<>(Arrays.asList(\"first\", \"second\"));\n    assertEquals(\"first,second\", String.join(\",\", s));\n}",
                "fixed": "@Test\npublic void testUniqueLabels() {\n    Set<String> s = new LinkedHashSet<>(Arrays.asList(\"first\", \"second\"));\n    assertEquals(\"first,second\", String.join(\",\", s));\n}",
            },
        ],
        "Handle Exception": [
            {
                "source_repo": "external/biojava-idiom-4",
                "flaky": "@Test\npublic void testCloseStream() throws Exception {\n    TempChannel ch = TempChannel.open();\n    ch.writeHeader(\"v1\");\n    assertEquals(\"v1\", ch.readHeader());\n}",
                "fixed": "@Test\npublic void testCloseStream() throws Exception {\n    try (TempChannel ch = TempChannel.open()) {\n        ch.writeHeader(\"v1\");\n        assertEquals(\"v1\", ch.readHeader());\n    }\n}",
            },
            {
                "source_repo": "external/okhttp-idiom-6",
                "flaky": "@Test\npublic void testParseInvalidPayload() {\n    PayloadReader.parse(\"{bad\");\n}",
                "fixed": "@Test\npublic void testParseInvalidPayload() {\n    try {\n        PayloadReader.parse(\"{bad\");\n        fail(\"Expected IllegalArgumentException\");\n    } catch (IllegalArgumentException expected) {\n        assertTrue(expected.getMessage().contains(\"bad\"));\n    }\n}",
            },
        ],
        "Miscellaneous": [
            {
                "source_repo": "external/okhttp-idiom-7",
                "flaky": "@Test\npublic void testWorkspaceWrite() throws IOException {\n    File out = new File(\"target/shared_out.txt\");\n    Files.write(out.toPath(), \"ok\".getBytes(StandardCharsets.UTF_8));\n    assertEquals(2L, out.length());\n}",
                "fixed": "@Test\npublic void testWorkspaceWrite() throws IOException {\n    Path out = Files.createTempFile(\"isolated_out_\", \".txt\");\n    try {\n        Files.write(out, \"ok\".getBytes(StandardCharsets.UTF_8));\n        assertEquals(2L, Files.size(out));\n    } finally {\n        Files.deleteIfExists(out);\n    }\n}",
            },
            {
                "source_repo": "external/biojava-idiom-5",
                "flaky": "@Test\npublic void testLocaleFormatting() {\n    assertEquals(\"3.14\", DecimalPrinter.formatTwo(3.14159));\n}",
                "fixed": "@Test\npublic void testLocaleFormatting() {\n    Locale prev = Locale.getDefault();\n    try {\n        Locale.setDefault(Locale.US);\n        assertEquals(\"3.14\", DecimalPrinter.formatTwo(3.14159));\n    } finally {\n        Locale.setDefault(prev);\n    }\n}",
            },
        ],
        "Change Data Format": [
            {
                "source_repo": "external/jackson-idiom-1",
                "flaky": "@Test\npublic void testSerializeAccount() throws Exception {\n    String json = JsonCodec.encode(new Account(\"u1\", 5));\n    assertEquals(\"{\\\"id\\\":\\\"u1\\\",\\\"tier\\\":5}\", json);\n}",
                "fixed": "@Test\npublic void testSerializeAccount() throws Exception {\n    String json = JsonCodec.encode(new Account(\"u1\", 5));\n    ObjectMapper mapper = new ObjectMapper();\n    assertEquals(mapper.readTree(\"{\\\"id\\\":\\\"u1\\\",\\\"tier\\\":5}\"), mapper.readTree(json));\n}",
            },
            {
                "source_repo": "external/jackson-idiom-2",
                "flaky": "@Test\npublic void testRenderCsvRow() {\n    String csv = CsvFormatter.toRow(new Record(\"a\", \"\"));\n    assertEquals(2, csv.split(\",\").length);\n}",
                "fixed": "@Test\npublic void testRenderCsvRow() {\n    String csv = CsvFormatter.toRow(new Record(\"a\", \"\"));\n    assertEquals(2, csv.split(\",\", -1).length);\n}",
            },
        ],
        "Reorder Parameters": [
            {
                "source_repo": "external/okhttp-idiom-8",
                "flaky": "@Test\npublic void testMergePairs() {\n    PairResult res = PairMerger.combine(\"b\", \"a\");\n    assertEquals(\"a:b\", res.canonicalKey());\n}",
                "fixed": "@Test\npublic void testMergePairs() {\n    PairResult res = PairMerger.combine(\"a\", \"b\");\n    assertEquals(\"a:b\", res.canonicalKey());\n}",
            },
            {
                "source_repo": "external/biojava-idiom-6",
                "flaky": "@Test\npublic void testRangeBounds() {\n    Interval iv = Interval.between(5, 5);\n    assertEquals(0, iv.span());\n}",
                "fixed": "@Test\npublic void testRangeBounds() {\n    Interval iv = Interval.ofOrdered(5, 5);\n    assertEquals(0, iv.span());\n}",
            },
        ],
        "Call Static Method": [
            {
                "source_repo": "external/okhttp-idiom-9",
                "flaky": "@Test\npublic void testMetricSnapshot() {\n    MetricBus.record(\"hits\", 3);\n    assertEquals(3, MetricBus.read(\"hits\"));\n}",
                "fixed": "@Test\npublic void testMetricSnapshot() {\n    MetricBus.reset();\n    MetricBus.record(\"hits\", 3);\n    assertEquals(3, MetricBus.read(\"hits\"));\n}",
            },
            {
                "source_repo": "external/jackson-idiom-3",
                "flaky": "@Test\npublic void testOrderedJsonWrite() {\n    String out = BeanWriter.toJsonSorted(new SampleBean(\"x\", 1));\n    assertEquals(\"{\\\"code\\\":1,\\\"name\\\":\\\"x\\\"}\", out);\n}",
                "fixed": "@Test\npublic void testOrderedJsonWrite() {\n    BeanWriter.enableDeterministicMapOrder(true);\n    String out = BeanWriter.toJsonSorted(new SampleBean(\"x\", 1));\n    assertEquals(\"{\\\"code\\\":1,\\\"name\\\":\\\"x\\\"}\", out);\n}",
            },
        ],
        "String Matching": [
            {
                "source_repo": "external/okhttp-idiom-10",
                "flaky": "@Test\npublic void testQueryString() {\n    String qs = QueryEncoder.build(mapOf(\"x\", \"1\", \"y\", \"2\"));\n    assertEquals(\"x=1&y=2\", qs);\n}",
                "fixed": "@Test\npublic void testQueryString() {\n    String qs = QueryEncoder.build(mapOf(\"x\", \"1\", \"y\", \"2\"));\n    List<String> parts = Arrays.asList(qs.split(\"&\", -1));\n    Collections.sort(parts);\n    assertEquals(Arrays.asList(\"x=1\", \"y=2\"), parts);\n}",
            },
            {
                "source_repo": "external/biojava-idiom-7",
                "flaky": "@Test\npublic void testSummaryAttributes() {\n    String s = NodeFormatter.describeAttrs(\"n1\");\n    assertEquals(\"node[a=1,b=2]\", s);\n}",
                "fixed": "@Test\npublic void testSummaryAttributes() {\n    String s = NodeFormatter.describeAttrs(\"n1\");\n    assertTrue(s.startsWith(\"node[\") && s.endsWith(\"]\"));\n    Set<String> kv = new HashSet<>(Arrays.asList(s.substring(5, s.length() - 1).split(\",\", -1)));\n    assertEquals(new HashSet<>(Arrays.asList(\"a=1\", \"b=2\")), kv);\n}",
            },
        ],
        "Change Timezone": [
            {
                "source_repo": "external/okhttp-idiom-11",
                "flaky": "@Test\npublic void testFormatEpoch() {\n    assertEquals(\"1970-01-01 00:00:00\", DatePrinter.formatMillis(0L));\n}",
                "fixed": "@Test\npublic void testFormatEpoch() {\n    TimeZone prev = TimeZone.getDefault();\n    try {\n        TimeZone.setDefault(TimeZone.getTimeZone(\"UTC\"));\n        assertEquals(\"1970-01-01 00:00:00\", DatePrinter.formatMillis(0L));\n    } finally {\n        TimeZone.setDefault(prev);\n    }\n}",
            },
            {
                "source_repo": "external/biojava-idiom-8",
                "flaky": "@Test\npublic void testUtcHeader() {\n    SimpleDateFormat df = new SimpleDateFormat(\"yyyy-MM-dd\");\n    assertEquals(\"2020-01-01\", df.format(new Date(1577836800000L)));\n}",
                "fixed": "@Test\npublic void testUtcHeader() {\n    SimpleDateFormat df = new SimpleDateFormat(\"yyyy-MM-dd\");\n    df.setTimeZone(TimeZone.getTimeZone(\"UTC\"));\n    assertEquals(\"2020-01-01\", df.format(new Date(1577836800000L)));\n}",
            },
        ],
        "Handle Timeout": [
            {
                "source_repo": "external/okhttp-idiom-12",
                "flaky": "@Test\npublic void testAsyncFlush() throws Exception {\n    AsyncBuffer buf = new AsyncBuffer();\n    buf.submit(\"item\");\n    Thread.sleep(10);\n    assertEquals(1, buf.flushedCount());\n}",
                "fixed": "@Test\npublic void testAsyncFlush() throws Exception {\n    AsyncBuffer buf = new AsyncBuffer();\n    buf.submit(\"item\");\n    buf.awaitFlush(5, TimeUnit.SECONDS);\n    assertEquals(1, buf.flushedCount());\n}",
            },
            {
                "source_repo": "external/guava-idiom-3",
                "flaky": "@Test(timeout = 20)\npublic void testHeavyDigest() {\n    assertEquals(64, HashEngine.sha256Hex(\"payload\").length());\n}",
                "fixed": "@Test(timeout = 5000)\npublic void testHeavyDigest() {\n    assertEquals(64, HashEngine.sha256Hex(\"payload\").length());\n}",
            },
        ],
    },
    "python": {
        "Change Assertion": [
            {
                "source_repo": "external/pluggy-idiom-1",
                "flaky": "def test_collect_hook_names():\n    names = registry.list_active_hooks()\n    assert names == [' setup', 'teardown']\n",
                "fixed": "def test_collect_hook_names():\n    names = registry.list_active_hooks()\n    assert sorted(names) == sorted([' setup', 'teardown'])\n    assert len(names) == 2\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-1",
                "flaky": "def test_header_items():\n    hdrs = build_default_headers()\n    assert list(hdrs.keys()) == ['Accept', 'User-Agent']\n",
                "fixed": "def test_header_items():\n    hdrs = build_default_headers()\n    assert set(hdrs.keys()) == {'Accept', 'User-Agent'}\n    assert hdrs['Accept'] and hdrs['User-Agent']\n",
            },
        ],
        "Change Condition": [
            {
                "source_repo": "external/pluggy-idiom-2",
                "flaky": "def test_cache_contains():\n    store = TokenStore()\n    store.add('t1')\n    if list(store.items())[0] == 't1':\n        assert store.count() == 1\n",
                "fixed": "def test_cache_contains():\n    store = TokenStore()\n    store.add('t1')\n    if 't1' in store.items():\n        assert store.count() == 1\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-2",
                "flaky": "def test_env_flag():\n    cfg = load_runtime_flags()\n    assert cfg.enabled is True\n",
                "fixed": "def test_env_flag(monkeypatch):\n    monkeypatch.delenv('DISABLE_RUNTIME_FLAGS', raising=False)\n    cfg = load_runtime_flags()\n    assert cfg.enabled is True\n",
            },
        ],
        "Reset Variable": [
            {
                "source_repo": "external/pluggy-idiom-3",
                "flaky": "def test_default_plugins():\n    assert PluginHub.registered_names() == []\n",
                "fixed": "def test_default_plugins():\n    PluginHub._registry.clear()\n    assert PluginHub.registered_names() == []\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-3",
                "flaky": "def test_counter_increment():\n    RequestCounter.increment()\n    assert RequestCounter.value == 1\n",
                "fixed": "def test_counter_increment():\n    orig = RequestCounter.value\n    try:\n        RequestCounter.value = 0\n        RequestCounter.increment()\n        assert RequestCounter.value == 1\n    finally:\n        RequestCounter.value = orig\n",
            },
        ],
        "Reorder Data": [
            {
                "source_repo": "external/pluggy-idiom-4",
                "flaky": "def test_discover_modules():\n    mods = scan_namespace_modules()\n    assert mods == ['alpha', 'beta', 'gamma']\n",
                "fixed": "def test_discover_modules():\n    mods = sorted(scan_namespace_modules())\n    assert mods == ['alpha', 'beta', 'gamma']\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-4",
                "flaky": "def test_route_pairs():\n    pairs = router.export_routes()\n    assert pairs == [('/a', 'get'), ('/b', 'post')]\n",
                "fixed": "def test_route_pairs():\n    pairs = sorted(router.export_routes())\n    assert pairs == [('/a', 'get'), ('/b', 'post')]\n",
            },
        ],
        "Change Data Structure": [
            {
                "source_repo": "external/pluggy-idiom-5",
                "flaky": "def test_unique_tags():\n    raw = {'b', 'a'}\n    assert ','.join(raw) == 'a,b'\n",
                "fixed": "def test_unique_tags():\n    raw = ['a', 'b']\n    assert ','.join(raw) == 'a,b'\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-5",
                "flaky": "def test_merge_configs():\n    merged = merge_dicts({'k': 1}, {'m': 2})\n    assert tuple(merged.items()) == (('k', 1), ('m', 2))\n",
                "fixed": "def test_merge_configs():\n    merged = dict(sorted(merge_dicts({'k': 1}, {'m': 2}).items()))\n    assert tuple(merged.items()) == (('k', 1), ('m', 2))\n",
            },
        ],
        "Handle Exception": [
            {
                "source_repo": "external/pluggy-idiom-6",
                "flaky": "def test_temp_config_load():\n    os.environ['APP_MODE'] = 'strict'\n    assert get_mode() == 'strict'\n",
                "fixed": "def test_temp_config_load():\n    old = os.environ.get('APP_MODE')\n    try:\n        os.environ['APP_MODE'] = 'strict'\n        assert get_mode() == 'strict'\n    finally:\n        if old is None:\n            os.environ.pop('APP_MODE', None)\n        else:\n            os.environ['APP_MODE'] = old\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-6",
                "flaky": "def test_bad_port():\n    parse_endpoint('host:notaport')\n",
                "fixed": "def test_bad_port():\n    with pytest.raises(ValueError):\n        parse_endpoint('host:notaport')\n",
            },
        ],
        "Miscellaneous": [
            {
                "source_repo": "external/pluggy-idiom-7",
                "flaky": "def test_write_report():\n    p = Path('build_report.json')\n    p.write_text('{\"ok\": true}')\n    assert p.exists()\n",
                "fixed": "def test_write_report(tmp_path):\n    p = tmp_path / 'build_report.json'\n    p.write_text('{\"ok\": true}')\n    assert p.read_text() == '{\"ok\": true}'\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-7",
                "flaky": "def test_working_dir_asset():\n    assert Path('assets/logo.svg').name == 'logo.svg'\n",
                "fixed": "def test_working_dir_asset():\n    orig_cwd = os.getcwd()\n    try:\n        assert Path('assets/logo.svg').name == 'logo.svg'\n    finally:\n        os.chdir(orig_cwd)\n",
            },
        ],
        "Change Data Format": [
            {
                "source_repo": "external/pluggy-idiom-8",
                "flaky": "def test_dump_payload():\n    s = dump_json_payload({'b': 2, 'a': 1})\n    assert s == '{\"a\": 1, \"b\": 2}'\n",
                "fixed": "def test_dump_payload():\n    s = dump_json_payload({'b': 2, 'a': 1})\n    assert json.loads(s) == {'a': 1, 'b': 2}\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-8",
                "flaky": "def test_query_encode():\n    qs = encode_params({'x': '1', 'y': '2'})\n    assert qs == 'x=1&y=2'\n",
                "fixed": "def test_query_encode():\n    qs = encode_params({'x': '1', 'y': '2'})\n    assert sorted(qs.split('&')) == ['x=1', 'y=2']\n",
            },
        ],
        "Reorder Parameters": [
            {
                "source_repo": "external/pluggy-idiom-9",
                "flaky": "def test_pair_key():\n    assert make_ordered_pair('z', 'a') == ('a', 'z')\n",
                "fixed": "def test_pair_key():\n    assert make_ordered_pair('a', 'z') == ('a', 'z')\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-9",
                "flaky": "def test_clamp_bounds():\n    assert clamp_value(10, 0, 5) == 5\n",
                "fixed": "def test_clamp_bounds():\n    assert clamp_value(10, low=0, high=5) == 5\n",
            },
        ],
        "Call Static Method": [
            {
                "source_repo": "external/pluggy-idiom-10",
                "flaky": "def test_singleton_client():\n    client = ServiceClient.instance()\n    assert client.endpoint == 'https://api.local'\n",
                "fixed": "def test_singleton_client():\n    ServiceClient.reset_instance()\n    client = ServiceClient.instance()\n    assert client.endpoint == 'https://api.local'\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-10",
                "flaky": "def test_schema_cache():\n    schema = SchemaLoader.get('v1')\n    assert schema.version == 'v1'\n",
                "fixed": "def test_schema_cache():\n    SchemaLoader.clear_cache()\n    schema = SchemaLoader.get('v1')\n    assert schema.version == 'v1'\n",
            },
        ],
        "String Matching": [
            {
                "source_repo": "external/pluggy-idiom-11",
                "flaky": "def test_repr_fields():\n    r = repr(ConfigBox(a=1, b=2))\n    assert r == 'ConfigBox(a=1, b=2)'\n",
                "fixed": "def test_repr_fields():\n    r = repr(ConfigBox(a=1, b=2))\n    assert r.startswith('ConfigBox(') and r.endswith(')')\n    assert sorted(r[10:-1].split(', ')) == ['a=1', 'b=2']\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-11",
                "flaky": "def test_cookie_header():\n    hdr = format_cookie_attrs({'Path': '/', 'Secure': 'true'})\n    assert hdr == 'Path=/; Secure=true'\n",
                "fixed": "def test_cookie_header():\n    hdr = format_cookie_attrs({'Path': '/', 'Secure': 'true'})\n    assert sorted(hdr.split('; ')) == ['Path=/', 'Secure=true']\n",
            },
        ],
        "Change Timezone": [
            {
                "source_repo": "external/pluggy-idiom-12",
                "flaky": "def test_epoch_iso():\n    assert format_local_day(0) == '1970-01-01'\n",
                "fixed": "def test_epoch_iso(monkeypatch):\n    monkeypatch.setenv('TZ', 'UTC')\n    assert format_local_day(0) == '1970-01-01'\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-12",
                "flaky": "def test_http_date():\n    assert to_utc_stamp(0).endswith('GMT')\n",
                "fixed": "def test_http_date():\n    os.environ['TZ'] = 'UTC'\n    assert to_utc_stamp(0).endswith('GMT')\n",
            },
        ],
        "Handle Timeout": [
            {
                "source_repo": "external/pluggy-idiom-13",
                "flaky": "def test_worker_drain():\n    w = BackgroundQueue()\n    w.push('x')\n    assert w.completed == ['x']\n",
                "fixed": "def test_worker_drain():\n    w = BackgroundQueue()\n    w.push('x')\n    w.join(timeout=5.0)\n    assert w.completed == ['x']\n",
            },
            {
                "source_repo": "external/werkzeug-idiom-13",
                "flaky": "def test_retry_backoff():\n    assert poll_until_ready(timeout=0.001) is True\n",
                "fixed": "def test_retry_backoff():\n    assert poll_until_ready(timeout=2.0) is True\n",
            },
        ],
    },
}


def verify_flakyfix_zero_leakage(all_samples: List[Dict[str, Any]]) -> None:
    """
    Programmatically verify ZERO overlap between FLAKYFIX_FEWSHOT_DB and all 208 attempted
    benchmark tests / 70 repositories. Raises AssertionError immediately on any violation.
    """
    bench_projects = {s["project_name"].lower() for s in all_samples}
    bench_methods = {s["victim_method"] for s in all_samples if s.get("victim_method")}
    bench_code_lines = set()
    for s in all_samples:
        for ln in (s.get("orig_test_code") or "").splitlines():
            st = ln.strip()
            if len(st) >= 28 and not st.startswith(("@Test", "try", "finally", "} catch", "with pytest.raises(")):
                bench_code_lines.add(st)

    for lang in ("java", "python"):
        assert set(FLAKYFIX_FEWSHOT_DB[lang].keys()) == set(FLAKYFIX_CATEGORIES), f"Missing categories in {lang}"
        for cat, pairs in FLAKYFIX_FEWSHOT_DB[lang].items():
            assert len(pairs) == 2, f"{lang}/{cat} must have 2 few-shot examples"
            for ex in pairs:
                repo_low = ex["source_repo"].lower()
                for bp in bench_projects:
                    assert bp not in repo_low, f"Leakage: benchmark repo {bp} in {repo_low}"
                for bm_name in bench_methods:
                    if len(bm_name) >= 8:
                        assert f" {bm_name}(" not in ex["flaky"], f"Method name collision: {bm_name}"
                for code_blob in (ex["flaky"], ex["fixed"]):
                    for ln in code_blob.splitlines():
                        st = ln.strip()
                        if len(st) >= 28:
                            assert st not in bench_code_lines, f"Code line overlap detected: {st}"


def build_flakyfix_category_prompt(sample: Dict[str, Any]) -> str:
    """Step 1 of FlakyFix (IEEE TSE 2024 Fig. 8): Predict repair category from TEST CODE ONLY."""
    lang = sample["language"]
    cats_bullet = "\n".join(f"- {c}" for c in FLAKYFIX_CATEGORIES)
    return (
        f"You are classifying the repair category needed to fix a flaky {lang.capitalize()} test "
        "by analyzing ONLY the flaky test method code (FlakyFix two-step protocol).\n\n"
        f"Candidate Repair Categories:\n{cats_bullet}\n\n"
        f"Flaky Test Code:\n```{lang}\n{sample['orig_test_code']}\n```\n\n"
        "Reply with ONLY the single exact category name from the list above (no explanation)."
    )


def parse_flakyfix_category(raw_resp: str) -> str:
    txt = (raw_resp or "").strip().lower()
    for c in FLAKYFIX_CATEGORIES:
        if c.lower() == txt:
            return c
    for c in FLAKYFIX_CATEGORIES:
        if c.lower() in txt:
            return c
    return "Miscellaneous"


def build_flakyfix_repair_prompt(
    sample: Dict[str, Any],
    predicted_category: str,
    round_idx: int = 1,
    prev_candidate: Optional[Dict[str, Any]] = None,
    feedback_msg: str = "",
) -> str:
    """
    Step 2 of FlakyFix (IEEE TSE 2024 Fig. 8):
      Test-code-only input + Predicted Fix Category + 2 Out-of-Benchmark Few-Shot Examples.
      Does NOT provide production source code, dynamic stack traces, or partner tests.
    """
    lang = sample["language"]
    examples = FLAKYFIX_FEWSHOT_DB[lang].get(predicted_category) or FLAKYFIX_FEWSHOT_DB[lang]["Miscellaneous"]

    ex_blocks = []
    for idx, ex in enumerate(examples, 1):
        ex_blocks.append(
            f"### Example {idx} (Fix Category: {predicted_category})\n"
            f"Flaky Test:\n```{lang}\n{ex['flaky'].strip()}\n```\n"
            f"Fixed Test:\n```{lang}\n{ex['fixed'].strip()}\n```"
        )

    iter_block = ""
    if round_idx > 1 and prev_candidate is not None:
        prev_code = (
            prev_candidate.get("test_code") or prev_candidate.get("victim_code") or ""
        )
        iter_block = (
            f"\n=== ITERATION {round_idx} EXECUTION FEEDBACK ===\n"
            f"Your previous candidate test:\n```{lang}\n{prev_code}\n```\n"
            f"Error / Failure Feedback:\n{feedback_msg}\n"
            "Please fix the flaky test method so it compiles and passes deterministically.\n"
        )

    if lang == "java":
        tag_name = "fix" if sample["test_type"] == "ID" else "victim"
        fmt = (
            "Output the repaired Java test method using the exact markers below:\n"
            f"//<{tag_name} start>\n...complete repaired Java test method...\n//<{tag_name} end>\n"
            "If you need any additional Java `import ...;` lines, place them inside:\n"
            "//<import start>\n//<import end>"
        )
    else:
        fmt = (
            "Output the repaired Python test function using the exact markers below:\n"
            "#<victim start>\n...complete repaired Python test function...\n#<victim end>\n"
            "If you need any additional Python `import ...` lines, place them inside:\n"
            "#<import start>\n#<import end>"
        )

    return (
        f"Fix the following flaky {lang.capitalize()} test using repair category `{predicted_category}`.\n"
        "Here are out-of-benchmark examples of this repair category:\n\n"
        + "\n\n".join(ex_blocks)
        + f"\n\n### Target Flaky Test (Predicted Category: {predicted_category})\n"
        f"```{lang}\n{sample['orig_test_code']}\n```\n"
        + iter_block
        + "\n"
        + fmt
    )
