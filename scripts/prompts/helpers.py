def build_file_blocks(files):
    blocks = []

    for filename, content in files.items():
        blocks.append(
            f"""
File: {filename}

~~~text
{content}
~~~
"""
        )

    return "\n\n".join(blocks)
