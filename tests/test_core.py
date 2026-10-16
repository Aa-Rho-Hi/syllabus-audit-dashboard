from app.matcher import parse_course_number, number_regex, regex_extract_objectives, name_score
from app.verbs import regex_check, split_objectives


def test_course_number_variants():
    rx = number_regex(parse_course_number("csce-489"))
    for s in ["CSCE 489", "CSCE489", "csce-489", "CSCE 489/689", "CSCE 689/489"]:
        assert rx.search(s), s
    assert not rx.search("CSCE 4890")
    assert not rx.search("ECEN 489")


def test_avoid_inflections_and_phrases():
    r = regex_check("1. Understood the basics\n2. Gain knowledge of TCP\n3. Recognises patterns\n4. Analyze logs")
    assert [x["status"] for x in r] == ["avoid", "avoid", "avoid", "ok"]
    assert r[1]["flags"][0]["base"] == "knowledge of"
    assert r[3]["bloom_levels"] == ["Analysis"]


def test_no_false_positives():
    # 'oversee', 'seed', 'experiment', 'known-plaintext' shouldn't trip 'see'/'experience'/'know'?
    r = regex_check("Design an experiment to oversee seed selection")
    assert r[0]["flags"] == [] and r[0]["status"] == "ok"


def test_leadin_and_stems():
    text = "Students will be able to:\n- Students will be able to compare ciphers\n(a) Break down a protocol"
    assert len(split_objectives(text)) == 2
    r = regex_check(text)
    assert r[0]["leading_verb"].lower() == "compare" and r[1]["leading_verb"].lower() == "break down"


def test_unlisted_verb():
    assert regex_check("Learn Python")[0]["status"] == "not_listed"


def test_extract_objectives():
    syl = """Course Description\nStuff.\n\nLearning Outcomes\nUpon completion of this course, students will be able to:\n1. Explain TLS.\n2. Design a protocol.\n\nGrading\nExams 50%"""
    assert regex_extract_objectives(syl) == ["Explain TLS.", "Design a protocol."]


def test_name_score():
    assert name_score("Software Engineering", "csce_606_software_engineering.txt", "") >= 90
    assert name_score("Quantum Basket Weaving", "csce_606_software_engineering.txt", "Agile") < 60


def test_messy_paste():
    messy = ("Upon successfully completing the course, students will be able to:\r\n\r\n"
             "1.   Analyze and design resistive DC circuits using fundamental circuit laws and\r\n"
             "techniques including node voltage analysis.\r\n\r\n\r\n"
             "\t•\tUnderstand  the transient response of RL circuits.\r\n"
             "    Design a  digital filter.\r\n"
             "o\tEvaluate amplifier performance using simulation and lab\r\n"
             "   measurements.\r\n"
             "  Know the TLS handshake.   \r\n\r\n")
    r = regex_check(messy)
    assert len(r) == 5
    assert [x["status"] for x in r] == ["ok", "avoid", "ok", "ok", "avoid"]
    assert r[0]["text"].endswith("node voltage analysis.")
