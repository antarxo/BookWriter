export class RenderedBook {
  constructor(data = {}) {
    this.version = "rendered-book-v1";
    this.pages = data.pages || [];
  }

  addPage(page) {
    this.pages.push(page);
  }

  getPage(id) {
    return this.pages.find(page => page.id === id) || null;
  }
}

export function createRenderedPage(id, width, height, items = []) {
  return { id, width, height, items };
}
