#include "../src/TVNCNavigationBack.h"
#include <cassert>
#include <fstream>
#include <iostream>
#include <string>

static TVNCBackHeader blank(int width, int height) {
    int side = std::min(width, height);
    TVNCBackHeader image;
    image.screenWidth = width; image.screenHeight = height;
    image.width = static_cast<int>(side * .20);
    image.height = static_cast<int>(side * .34);
    image.rgb.assign(image.width * image.height, 0xf2f2f7);
    return image;
}
static void mark(TVNCBackHeader &image, int x, int y) {
    if (x >= 0 && x < image.width && y >= 0 && y < image.height)
        image.rgb[y * image.width + x] = 0x007aff;
}
static void arrow(TVNCBackHeader &image, bool shaft) {
    int side = std::min(image.screenWidth, image.screenHeight);
    int tip = static_cast<int>(side * .035), cy = static_cast<int>(side * .12);
    int half = std::max(5, static_cast<int>(side * .025));
    for (int dy = -half; dy <= half; ++dy)
        for (int thickness = 0; thickness < std::max(1, half / 5); ++thickness)
            mark(image, tip + std::abs(dy) + thickness, cy + dy);
    if (shaft) for (int x = tip; x < tip + half * 2; ++x)
        for (int y = cy; y < cy + std::max(1, half / 5); ++y) mark(image, x, y);
}
int main(int argc, char **argv) {
    if (argc == 4) {
        int w = std::stoi(argv[2]), h = std::stoi(argv[3]);
        auto image = blank(w, h);
        std::ifstream file(argv[1], std::ios::binary);
        for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
            unsigned char rgb[3]; file.read(reinterpret_cast<char *>(rgb), 3);
            if (y < image.height && x < image.width)
                image.rgb[y * image.width + x] = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2];
        }
        assert(file);
        auto target = TVNCFindBackArrow(image);
        std::cout << target.found << ' ' << target.x << ' ' << target.y << '\n';
        return 0;
    }
    for (int rotation = 0; rotation < 4; ++rotation) {
        TVNCBackTarget original{41, 95, true}, rotated = original;
        if (rotation == 1) rotated = {1338 - 1 - original.y, original.x, true};
        if (rotation == 2) rotated = {752 - 1 - original.x, 1338 - 1 - original.y, true};
        if (rotation == 3) rotated = {original.y, 752 - 1 - original.x, true};
        auto result = TVNCBackPhysicalPoint(rotated, rotation, 752, 1338);
        assert(result.found && result.x == original.x && result.y == original.y);
    }
    assert(!TVNCBackPhysicalPoint({-1, 50, true}, 0, 752, 1338).found);
    assert(!TVNCBackPhysicalPoint({}, 0, 752, 1338).found);
    for (int size : {131, 263, 375, 752, 1179}) for (bool landscape : {false, true}) {
        auto image = blank(landscape ? size * 2 : size, landscape ? size : size * 2);
        assert(!TVNCFindBackArrow(image).found);
        for (bool shaft : {false, true}) {
            auto valid = image; arrow(valid, shaft);
            assert(TVNCFindBackArrow(valid).found);
        }
        int cx = static_cast<int>(size * .08), cy = static_cast<int>(size * .12);
        int half = std::max(5, static_cast<int>(size * .025));
        for (int shape = 0; shape < 5; ++shape) {
            auto negative = image;
            for (int dy = -half; dy <= half; ++dy) for (int dx = -half; dx <= half; ++dx) {
                bool draw = shape == 0 ? (std::abs(dx - dy) <= 1 || std::abs(dx + dy) <= 1) :
                            shape == 1 ? (std::abs(dx) <= 1 || std::abs(dy) <= 1) :
                            shape == 2 ? std::abs(dx * dx + dy * dy - half * half) <= half * 2 :
                            shape == 3 ? (std::abs(dy) <= 1 || std::abs(dy - half) <= 1 || std::abs(dy + half) <= 1) :
                                         dx >= std::abs(dy) - half;
                if (draw) mark(negative, cx + dx, cy + dy);
            }
            assert(!TVNCFindBackArrow(negative).found);
        }
    }
    std::cout << "navigation back shapes: OK\n";
}
