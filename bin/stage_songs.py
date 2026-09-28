import os

MAP = [
    ("a_lonely_night", "The Weeknd - A Lonely Night.flac", "The Weeknd", "A Lonely Night"),
    ("after_hours", "The Weeknd - After Hours.flac", "The Weeknd", "After Hours"),
    ("best_friends", "The Weeknd - Best Friends.flac", "The Weeknd", "Best Friends"),
    ("blinding_lights", "The Weeknd - Blinding Lights.flac", "The Weeknd", "Blinding Lights"),
    ("cry_for_me", "The Weeknd - Cry For Me (Explicit).flac", "The Weeknd", "Cry For Me"),
    ("dancing_in_the_flames", "The Weeknd - Dancing In The Flames.flac", "The Weeknd", "Dancing In The Flames"),
    ("die_for_you", "The Weeknd - Die For You.flac", "The Weeknd", "Die For You"),
    ("earned_it", "The Weeknd - Earned It (From The _Fifty Shades Of Grey_ Soundtrack).flac", "The Weeknd", "Earned It"),
    ("how_do_i_make_you_love_me", "The Weeknd - How Do I Make You Love Me_.flac", "The Weeknd", "How Do I Make You Love Me"),
    ("is_there_someone_else", "The Weeknd - Is There Someone Else_.flac", "The Weeknd", "Is There Someone Else?"),
    ("less_than_zero", "The Weeknd - Less Than Zero.flac", "The Weeknd", "Less Than Zero"),
    ("niagara_falls", "The Weeknd - Niagara Falls (Explicit).flac", "The Weeknd", "Niagara Falls"),
    ("ordinary_life", "The Weeknd - Ordinary Life.flac", "The Weeknd", "Ordinary Life"),
    ("out_of_time", "The Weeknd - Out of Time.flac", "The Weeknd", "Out of Time"),
    ("reminder", "The Weeknd - Reminder (Explicit).flac", "The Weeknd", "Reminder"),
    ("sacrifice", "The Weeknd - Sacrifice (Explicit).flac", "The Weeknd", "Sacrifice"),
    ("save_your_tears", "The Weeknd - Save Your Tears (Explicit).flac", "The Weeknd", "Save Your Tears"),
    ("starry_eyes", "The Weeknd - Starry Eyes.flac", "The Weeknd", "Starry Eyes"),
    ("the_hills", "The Weeknd - The Hills (Explicit).flac", "The Weeknd", "The Hills"),
    ("true_colors", "The Weeknd - True Colors.flac", "The Weeknd", "True Colors"),
    ("wake_me_up", "The Weeknd _ Justice - Wake Me Up.flac", "The Weeknd & Justice", "Wake Me Up"),
    ("the_abyss", "The Weeknd _ Lana Del Rey - The Abyss.flac", "The Weeknd & Lana Del Rey", "The Abyss"),
    ("love_me_harder", "Ariana Grande _ The Weeknd - Love Me Harder.flac", "Ariana Grande & The Weeknd", "Love Me Harder"),
    ("creepin", "Metro Boomin _ The Weeknd _ 21 Savage - Creepin' (Explicit).flac", "Metro Boomin & The Weeknd & 21 Savage", "Creepin'"),
    ("moth_to_a_flame", "Swedish House Mafia _ The Weeknd - Moth To A Flame.flac", "Swedish House Mafia & The Weeknd", "Moth To A Flame"),
]

SRC = r"D:\DJ_agent\weeknd"
STAGE = r"D:\LLM_work\lofiremix\work\src"
os.makedirs(STAGE, exist_ok=True)

missing = []
with open(r"D:\LLM_work\lofiremix\work\songs.tsv", "w", encoding="utf-8") as tsv:
    for sid, fname, artist, title in MAP:
        src = os.path.join(SRC, fname)
        if not os.path.exists(src):
            missing.append(fname); continue
        dst = os.path.join(STAGE, sid + ".flac")
        if not os.path.exists(dst):
            try:
                os.link(src, dst)      # 同盘硬链接, 0 拷贝
            except OSError:
                import shutil
                shutil.copy2(src, dst)
        tsv.write(f"{sid}|{artist}|{title}\n")

n = len([f for f in os.listdir(STAGE) if f.endswith(".flac")])
print(f"staged {n}/25 flac (hardlink), missing: {missing or '无'}")
