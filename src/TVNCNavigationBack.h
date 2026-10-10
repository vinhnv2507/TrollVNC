#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

// Only inspect the leading navigation-bar area. Reject ambiguous shapes:
// a missing arrow is preferable to opening a menu/profile at a guessed point.
struct TVNCBackHeader {
    int screenWidth = 0, screenHeight = 0, width = 0, height = 0;
    std::vector<uint32_t> rgb;
};
struct TVNCBackTarget { int x = 0, y = 0; bool found = false; };

inline TVNCBackTarget TVNCBackPhysicalPoint(TVNCBackTarget p, int rotation, int width, int height) {
    if (!p.found || width <= 0 || height <= 0) return {};
    int x = p.x, y = p.y;
    switch (rotation & 3) {
        case 1: x = p.y; y = height - 1 - p.x; break;
        case 2: x = width - 1 - p.x; y = height - 1 - p.y; break;
        case 3: x = width - 1 - p.y; y = p.x; break;
    }
    if (x < 0 || x >= width || y < 0 || y >= height) return {};
    return {x, y, true};
}

inline TVNCBackTarget TVNCFindBackArrow(const TVNCBackHeader &image) {
    int shortSide = std::min(image.screenWidth, image.screenHeight);
    int w = image.width, h = image.height;
    if (shortSide < 96 || w < 8 || h < 8 ||
        image.rgb.size() != static_cast<size_t>(w) * h) return {};
    std::vector<uint8_t> mask(static_cast<size_t>(w) * h);
    int yStart = std::max(2, static_cast<int>(shortSide * .045));
    for (int y = yStart; y < h; ++y) {
        // The outer margin is background even beside a text-labelled Back.
        uint32_t background = image.rgb[y * w + std::max(2, w / 20)];
        for (int x = 2; x < w; ++x) {
            uint32_t value = image.rgb[y * w + x];
            int delta = 0;
            for (int shift : {0, 8, 16})
                delta = std::max(delta, std::abs(static_cast<int>((value >> shift) & 255) -
                                               static_cast<int>((background >> shift) & 255)));
            mask[y * w + x] = delta >= 40;
        }
    }
    TVNCBackTarget found;
    for (int y = yStart; y < h; ++y) for (int x = 2; x < w; ++x) {
        if (!mask[y * w + x]) continue;
        std::vector<int> component{y * w + x};
        mask[y * w + x] = 0;
        int minX = x, maxX = x, minY = y, maxY = y;
        for (size_t i = 0; i < component.size(); ++i) {
            int px = component[i] % w, py = component[i] / w;
            minX = std::min(minX, px); maxX = std::max(maxX, px);
            minY = std::min(minY, py); maxY = std::max(maxY, py);
            for (int dy = -1; dy <= 1; ++dy) for (int dx = -1; dx <= 1; ++dx) {
                int nx = px + dx, ny = py + dy;
                if (nx < 2 || nx >= w || ny < yStart || ny >= h) continue;
                if (mask[ny * w + nx]) {
                    mask[ny * w + nx] = 0;
                    component.push_back(ny * w + nx);
                }
            }
        }
        int cw = maxX - minX + 1, ch = maxY - minY + 1;
        double fill = static_cast<double>(component.size()) / (cw * ch);
        if (ch < std::max(4, static_cast<int>(shortSide * .012)) || ch > shortSide * .085 ||
            cw < ch * .3 || cw > ch * 1.8 || minX <= 2 || maxX >= w - 1 ||
            minY <= yStart || maxY >= h - 1 || fill < .10 || fill > .55) continue;
        std::vector<int> left(ch, w), right(ch, -1);
        for (int pixel : component) {
            int row = pixel / w - minY, col = pixel % w;
            left[row] = std::min(left[row], col); right[row] = std::max(right[row], col);
        }
        // The left edge of a '<' is linear in distance from its centre.
        // Row widths reject X, circles, triangles and hamburger/plus icons.
        double sx = 0, sy = 0, sxx = 0, sxy = 0, syy = 0;
        int n = 0, narrow = 0;
        for (int row = 0; row < ch; ++row) {
            double distance = std::abs(row - (ch - 1) / 2.0);
            if (distance < ch * .12 || distance > ch * .40 || right[row] < 0) continue;
            double value = left[row] - minX;
            sx += distance; sy += value; sxx += distance * distance;
            sxy += distance * value; syy += value * value; ++n;
            if (right[row] - left[row] + 1 <= std::max(2.0, ch * .27)) ++narrow;
        }
        double variance = n * sxx - sx * sx, covariance = n * sxy - sx * sy;
        double yVariance = n * syy - sy * sy;
        if (n < 4 || narrow < n * .85 || variance <= 0 || yVariance <= 0) continue;
        double slope = covariance / variance;
        double r2 = covariance * covariance / (variance * yVariance);
        int middle = ch / 2;
        if (slope < .35 || slope > 1.45 || r2 < .72 ||
            left[middle] > minX + std::max(2.0, ch * .12)) continue;
        if (found.found) return {}; // Two candidates: do not guess.
        found = {(minX + maxX) / 2, (minY + maxY) / 2, true};
    }
    return found;
}
