// test_sse_parser.js
const assert = require('assert');

async function simulateReadAdvisorStream(chunks) {
    let chunkIndex = 0;
    
    // Mock reader
    const reader = {
        read: async () => {
            if (chunkIndex >= chunks.length) {
                return { done: true, value: undefined };
            }
            return { done: false, value: chunks[chunkIndex++] };
        }
    };
    
    const decoder = new TextDecoder();
    let accumulated = '';
    let buffer = '';

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        const raw = decoder.decode(value, { stream: true });
        buffer += raw;
        const lines = buffer.split('\n');
        buffer = lines.pop(); // last element may be an incomplete line -- carry it over
        
        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const chunk = line.slice(6);  // strip "data: "
                accumulated += chunk;
            }
        }
    }
    
    return accumulated;
}

async function runTest() {
    const textEncoder = new TextEncoder();
    
    // Test 1: A normal chunk
    const chunk1 = textEncoder.encode("data: Hello, ");
    const chunk2 = textEncoder.encode("World!\n\ndata:  How are you?\n\n");
    const result1 = await simulateReadAdvisorStream([chunk1, chunk2]);
    assert.strictEqual(result1, "Hello, World! How are you?");
    console.log("Test 1 passed: Normal chunk and complete chunk.");

    // Test 2: Split "data: " prefix itself! (though rare, we handle standard mid-line splits)
    // Wait, the fix is just buffering. If "da" and "ta: hi" are split, buffer handles it.
    const chunk3 = textEncoder.encode("da");
    const chunk4 = textEncoder.encode("ta: This is a test\n\n");
    const result2 = await simulateReadAdvisorStream([chunk3, chunk4]);
    assert.strictEqual(result2, "This is a test");
    console.log("Test 2 passed: Split 'data: ' prefix.");

    // Test 3: Split mid-line
    const chunk5 = textEncoder.encode("data: This line is spl");
    const chunk6 = textEncoder.encode("it right in the ");
    const chunk7 = textEncoder.encode("middle.\n\n");
    const result3 = await simulateReadAdvisorStream([chunk5, chunk6, chunk7]);
    assert.strictEqual(result3, "This line is split right in the middle.");
    console.log("Test 3 passed: Split mid-line correctly reassembled.");
    
    console.log("All parsing tests passed successfully!");
}

runTest().catch(console.error);
