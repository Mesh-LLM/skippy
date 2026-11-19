import type { PageMapItem } from "nextra";
import { getPageMap } from "nextra/page-map";

type BlogFrontMatter = {
  title?: string;
  description?: string;
  date?: string;
  release?: string;
};

type BlogPost = Required<Pick<BlogFrontMatter, "title" | "description" | "date">> & {
  release?: string;
  route: string;
};

function postFromPage(item: PageMapItem): BlogPost | undefined {
  if (!("route" in item) || "children" in item || item.route === "/blog") {
    return;
  }

  const frontMatter = item.frontMatter as BlogFrontMatter | undefined;
  if (!frontMatter?.title || !frontMatter.description || !frontMatter.date) {
    return;
  }

  return {
    title: frontMatter.title,
    description: frontMatter.description,
    date: frontMatter.date,
    release: frontMatter.release,
    route: item.route,
  };
}

function formatDate(date: string): string {
  return new Intl.DateTimeFormat("en", {
    day: "numeric",
    month: "long",
    timeZone: "UTC",
    year: "numeric",
  }).format(new Date(`${date}T00:00:00Z`));
}

export async function BlogIndex() {
  const posts = (await getPageMap("/blog"))
    .map(postFromPage)
    .filter((post): post is BlogPost => Boolean(post))
    .sort((left, right) => right.date.localeCompare(left.date));

  if (posts.length === 0) {
    return <p>Release notes and engineering updates will appear here.</p>;
  }

  return (
    <section className="skippy-blog-list" aria-label="Skippy posts">
      {posts.map((post) => (
        <a className="skippy-blog-card" href={`${post.route}/`} key={post.route}>
          <article>
            <div className="skippy-blog-card__meta">
              <time dateTime={post.date}>{formatDate(post.date)}</time>
              {post.release ? (
                <span className="skippy-blog-card__release">Release {post.release}</span>
              ) : null}
            </div>
            <h2>{post.title}</h2>
            <p>{post.description}</p>
          </article>
        </a>
      ))}
    </section>
  );
}
