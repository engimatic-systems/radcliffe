package works.ravd.radcliffe

import org.junit.Assert.assertEquals
import org.junit.Test

class ScaffoldTest {
    @Test
    fun packageNameIsStable() {
        assertEquals("works.ravd.radcliffe", MainActivity::class.java.packageName)
    }
}
