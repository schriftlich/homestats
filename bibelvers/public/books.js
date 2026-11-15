// Bücher der Bibel: [Name, Kurzform, Kapitelzahl]. Nummer = Index + 1 (getbible-Zählung).
// Joel und Maleachi: je nach Übersetzung 3 oder 4 Kapitel – hier das Maximum.
const BOOKS = [
  ["1. Mose", "1Mo", 50], ["2. Mose", "2Mo", 40], ["3. Mose", "3Mo", 27], ["4. Mose", "4Mo", 36], ["5. Mose", "5Mo", 34],
  ["Josua", "Jos", 24], ["Richter", "Ri", 21], ["Rut", "Rt", 4], ["1. Samuel", "1Sam", 31], ["2. Samuel", "2Sam", 24],
  ["1. Könige", "1Kön", 22], ["2. Könige", "2Kön", 25], ["1. Chronik", "1Chr", 29], ["2. Chronik", "2Chr", 36],
  ["Esra", "Esr", 10], ["Nehemia", "Neh", 13], ["Ester", "Est", 10], ["Hiob", "Hi", 42], ["Psalm", "Ps", 150],
  ["Sprüche", "Spr", 31], ["Prediger", "Pred", 12], ["Hoheslied", "Hld", 8], ["Jesaja", "Jes", 66], ["Jeremia", "Jer", 52],
  ["Klagelieder", "Klgl", 5], ["Hesekiel", "Hes", 48], ["Daniel", "Dan", 12], ["Hosea", "Hos", 14], ["Joel", "Joel", 4],
  ["Amos", "Am", 9], ["Obadja", "Ob", 1], ["Jona", "Jon", 4], ["Micha", "Mi", 7], ["Nahum", "Nah", 3], ["Habakuk", "Hab", 3],
  ["Zefanja", "Zef", 3], ["Haggai", "Hag", 2], ["Sacharja", "Sach", 14], ["Maleachi", "Mal", 4],
  ["Matthäus", "Mt", 28], ["Markus", "Mk", 16], ["Lukas", "Lk", 24], ["Johannes", "Joh", 21], ["Apostelgeschichte", "Apg", 28],
  ["Römer", "Röm", 16], ["1. Korinther", "1Kor", 16], ["2. Korinther", "2Kor", 13], ["Galater", "Gal", 6], ["Epheser", "Eph", 6],
  ["Philipper", "Phil", 4], ["Kolosser", "Kol", 4], ["1. Thessalonicher", "1Thess", 5], ["2. Thessalonicher", "2Thess", 3],
  ["1. Timotheus", "1Tim", 6], ["2. Timotheus", "2Tim", 4], ["Titus", "Tit", 3], ["Philemon", "Phlm", 1], ["Hebräer", "Hebr", 13],
  ["Jakobus", "Jak", 5], ["1. Petrus", "1Petr", 5], ["2. Petrus", "2Petr", 3], ["1. Johannes", "1Joh", 5], ["2. Johannes", "2Joh", 1],
  ["3. Johannes", "3Joh", 1], ["Judas", "Jud", 1], ["Offenbarung", "Offb", 22],
];

// Gemeinfreie deutsche Übersetzungen bei getbible.net (Fallback, falls die Liste nicht geladen werden kann).
const TRANSLATIONS = [
  { id: "schlachter", name: "Schlachter (1951)" },
  { id: "elberfelder", name: "Elberfelder (1871)" },
  { id: "elberfelder1905", name: "Elberfelder (1905)" },
  { id: "luther1545", name: "Luther (1545)" },
];
