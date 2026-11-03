// Remove the background of a product photo (white background) with macOS Vision,
// the same subject-lifting Keynote's "Remove Background" uses.
//
//   kn-cutout <input image> <output.png>
//
// Writes a PNG with transparency, cropped to the subject. Exit code 3 = no subject found.
import AppKit
import CoreImage
import Vision

let args = CommandLine.arguments
guard args.count == 3 else {
    FileHandle.standardError.write("usage: kn-cutout <input> <output.png>\n".data(using: .utf8)!)
    exit(2)
}
let input = URL(fileURLWithPath: args[1])
let output = URL(fileURLWithPath: args[2])
guard let image = CIImage(contentsOf: input, options: [.applyOrientationProperty: true]) else {
    FileHandle.standardError.write("cannot read image\n".data(using: .utf8)!)
    exit(2)
}
let handler = VNImageRequestHandler(ciImage: image, options: [:])
let request = VNGenerateForegroundInstanceMaskRequest()
do {
    try handler.perform([request])
    guard let result = request.results?.first, !result.allInstances.isEmpty else {
        FileHandle.standardError.write("no subject found\n".data(using: .utf8)!)
        exit(3)
    }
    let buffer = try result.generateMaskedImage(ofInstances: result.allInstances, from: handler,
                                                croppedToInstancesExtent: true)
    let masked = CIImage(cvPixelBuffer: buffer)
    let context = CIContext()
    try context.writePNGRepresentation(of: masked, to: output, format: .RGBA8,
                                       colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
    print("\(Int(masked.extent.width))x\(Int(masked.extent.height))")
} catch {
    FileHandle.standardError.write("\(error)\n".data(using: .utf8)!)
    exit(1)
}
