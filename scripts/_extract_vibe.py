
import tarfile, os
src = 'C:/Users/YZP/WorkBuddy/Claw/方法论与研究文档/vibe.tar.gz'
dst = 'C:/Users/YZP/WorkBuddy/Claw/方法论与研究文档/Vibe-Research'
os.makedirs(dst, exist_ok=True)
with tarfile.open(src, 'r:gz') as tf:
    # strip top-level dir
    members = tf.getmembers()
    print('members:', len(members))
    top = members[0].name.split('/')[0]
    print('top dir:', top)
    for m in members:
        rel = m.name[len(top) + 1:] if m.name.startswith(top + '/') else m.name
        if not rel:
            continue
        target = os.path.join(dst, rel.replace('/', os.sep))
        if m.isdir():
            os.makedirs(target, exist_ok=True)
        elif m.isfile():
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tf.extractfile(m) as f, open(target, 'wb') as out:
                out.write(f.read())
print('extracted to', dst)

