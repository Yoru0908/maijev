from flows.maijev import glossary as g


def test_parse_row_reads_kind_keys_and_drops_note():
    assert g.parse_row('[人名] 野比のび太 / のび太／ノビ君 -> 大雄  # 主角') == (
        '人名', ['野比のび太', 'のび太', 'ノビ君'], '大雄')
    assert g.parse_row('ねこがみ -> 猫神') == ('', ['ねこがみ'], '猫神')
    assert g.parse_row('#ババババンビ -> #ババババンビ') == (
        '', ['#ババババンビ'], '#ババババンビ')
    assert g.parse_row('【本片主题】深夜电台') is None
    assert g.parse_row('ねこがみ ->') is None


def test_later_layer_takes_over_the_entity_it_names():
    table = g.merge([
        ('[人名] 野比のび太 / のび太 / ノビ君 -> 野比のび太\n'
         '[人名] 剛田武 / ジャイアン -> 剛田武', False),
        ('ノビ君 -> 大雄\n剛田武 -> 胖虎', True),
    ])
    assert [(e.keys, e.tgt) for e in table.entities] == [
        (['野比のび太', 'のび太', 'ノビ君'], '大雄'),
        (['剛田武', 'ジャイアン'], '胖虎'),
    ]
    assert table.entities[0].human_keys == {'ノビ君'}


def test_row_hitting_a_rendering_chains_and_two_hits_merge():
    table = g.merge([
        ('オールナイトニッポンゼロ -> オールナイトニッポン0\nANN -> ANN', False),
        ('オールナイトニッポン0 / ANN -> ANN0', True),
    ])
    assert len(table.entities) == 1
    ent = table.entities[0]
    assert ent.tgt == 'ANN0'
    assert set(ent.keys) == {'オールナイトニッポンゼロ', 'オールナイトニッポン0', 'ANN'}


def test_render_keeps_as_written_rows_unless_a_human_overrides():
    table = g.merge([
        ('【本片主题】主题\n[地名] 櫻坂46 -> 櫻坂46\n[节目] ANN0 -> ANN0', False),
        ('ANN0 -> オールナイトニッポン0\n過去曲メモ', True),
    ])
    block = g.render(table)
    assert '[地名] 櫻坂46 -> 櫻坂46' in block
    assert '[节目] ANN0 -> オールナイトニッポン0' in block
    assert block.count('ANN0') == 1
    assert block.index('主题') < block.index('櫻坂46') < block.index('過去曲メモ')


def test_enforcement_only_human_kana_keys_that_are_safe_to_replace():
    table = g.merge([
        ('[人名] 静香 / 静か / しずか -> 静香', False),
        ('しずかちゃん / しずか -> 静香\n山川 -> 山川宇衣\nのび太 -> 野比のび太\nえ -> 诶', True),
    ])
    # 静か is a pre-pass guess, 山川 is kanji, のび太 sits inside its own
    # rendering and え is too short: none of them may be rewritten
    assert g.enforcement(table) == {'しずかちゃん': '静香', 'しずか': '静香'}


def test_enforce_replaces_longest_key_in_one_pass():
    rules = {'しずか': '静香', 'しずかちゃん': '静香'}
    assert g.enforce('しずかちゃん和しずか', rules) == ('静香和静香', 2)
    assert g.enforce('没有日文', {}) == ('没有日文', 0)
